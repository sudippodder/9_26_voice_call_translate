"""Call-management service — DB + Redis + LiveKit token minting.

This module is the *only* place that creates Call rows and issues LiveKit
tokens, so that policy stays consistent. It is **never** on the realtime
audio path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Call, CallParticipant, Usage, User, UserLanguagePreference
from app.db.session import get_session
from app.livekit import create_agent_token, create_participant_token
from app.models.schemas import (
    CallAcceptResponse,
    CallCreate,
    CallCreateResponse,
    CallOut,
    CallParticipantOut,
)
from app.redis import enqueue_agent_join, set_call_state
from app.languages import is_supported


class CallNotFoundError(Exception):
    pass


class CallValidationError(Exception):
    pass


async def _validate_languages(*codes: str) -> None:
    for c in codes:
        if not is_supported(c):
            raise CallValidationError(f"Unsupported language code: {c}")


async def _get_user_preferences(db: AsyncSession, user_id: str) -> tuple[str, str]:
    """Return (speak, hear) for a user, defaulting to (en, hi) if unset."""
    stmt = select(UserLanguagePreference).where(
        UserLanguagePreference.user_id == user_id
    ).order_by(UserLanguagePreference.updated_at.desc()).limit(1)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row:
        return row.speak, row.hear
    return "en", "hi"


async def _verify_user_exists(db: AsyncSession, user_id: str) -> User:
    user = await db.get(User, user_id)
    if not user:
        raise CallValidationError(f"Unknown callee_id: {user_id}")
    return user


async def create_call(
    caller_id: str,
    payload: CallCreate,
) -> CallCreateResponse:
    """Create a call row, persist participants, mint a LiveKit token for the caller.

    The caller's languages come from the request payload.
    The receiver's languages are looked up from their saved preferences;
    if missing, the inverse of the caller's pair is used.
    """
    await _validate_languages(payload.source_language, payload.target_language)

    async with get_session() as db:
        # Verify callee exists
        await _verify_user_exists(db, payload.callee_id)

        caller_speak = payload.source_language
        caller_hear = payload.target_language
        # Look up receiver's saved preferences (their speak + hear)
        receiver_speak, receiver_hear = await _get_user_preferences(db, payload.callee_id)
        # If receiver's hear matches caller's speak, they're consistent already.
        # If receiver has no prefs, use the inverse of the caller's choice.
        if receiver_speak == "en" and receiver_hear == "hi" and payload.callee_id != caller_id:
            # Only override if no real preferences were saved
            stmt_p = select(UserLanguagePreference).where(
                UserLanguagePreference.user_id == payload.callee_id
            )
            existing = (await db.execute(stmt_p)).scalar_one_or_none()
            if not existing:
                receiver_speak = payload.target_language   # receiver speaks what caller hears
                receiver_hear = payload.source_language    # receiver hears what caller speaks

        await _validate_languages(caller_speak, caller_hear, receiver_speak, receiver_hear)

        call = Call(
            caller_id=caller_id,
            receiver_id=payload.callee_id,
            status="pending",
        )
        db.add(call)
        await db.flush()

        # Two participants, with their respective language pairs.
        caller_p = CallParticipant(
            call_id=call.id,
            user_id=caller_id,
            source_language=caller_speak,
            target_language=caller_hear,
        )
        receiver_p = CallParticipant(
            call_id=call.id,
            user_id=payload.callee_id,
            source_language=receiver_speak,
            target_language=receiver_hear,
        )
        db.add_all([caller_p, receiver_p])
        await db.flush()

        # Mint caller's LiveKit token — caller publishes their mic + subscribes
        # to the translated track destined for them.
        token = create_participant_token(
            call_id=call.id,
            user_id=caller_id,
            name=caller_id,
        )

        # Cache active call state in Redis (no audio — just metadata)
        await set_call_state(
            call.id,
            {
                "call_id": call.id,
                "caller_id": caller_id,
                "receiver_id": payload.callee_id,
                "status": "pending",
                "languages": {
                    "caller": {"speak": caller_speak, "hear": caller_hear},
                    "receiver": {"speak": receiver_speak, "hear": receiver_hear},
                },
                "room_name": token.room_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            ttl=86400,
        )

        return CallCreateResponse(
            call_id=call.id,
            room_name=token.room_name,
            livekit_url=token.url,
            participant_token=token.token,
        )


async def accept_call(call_id: str, user_id: str) -> CallAcceptResponse:
    """Receiver accepts the call — moves to `active` and mints their token.

    This is also the moment we dispatch the translation agent to join the room.
    """
    async with get_session() as db:
        call = await db.get(Call, call_id)
        if not call:
            raise CallNotFoundError(call_id)
        if call.receiver_id != user_id:
            raise CallValidationError("Only the receiver can accept this call")
        if call.status not in ("pending", "ringing"):
            raise CallValidationError(f"Call cannot be accepted in status={call.status}")

        call.status = "active"
        call.started_at = datetime.now(timezone.utc)

        # Mark both participants joined
        stmt = select(CallParticipant).where(CallParticipant.call_id == call_id)
        rows = (await db.execute(stmt)).scalars().all()
        participants_meta: list[dict] = []
        for r in rows:
            if r.joined_at is None:
                r.joined_at = datetime.now(timezone.utc)
            participants_meta.append({
                "participant_id": r.user_id,
                "speak": r.source_language,
                "hear": r.target_language,
            })

        token = create_participant_token(
            call_id=call.id,
            user_id=user_id,
            name=user_id,
        )

        # ----- Dispatch the translation agent -----
        # Mint an agent token and push a "join this room" command to Redis.
        # The agent process BLPOPs the queue and connects to the room.
        agent_token = create_agent_token(call.id)
        await enqueue_agent_join(
            call_id=call.id,
            room_name=token.room_name,
            livekit_url=token.url,
            agent_token=agent_token.token,
            participants=participants_meta,
        )

        return CallAcceptResponse(
            call_id=call.id,
            room_name=token.room_name,
            livekit_url=token.url,
            participant_token=token.token,
        )


async def reject_call(call_id: str, user_id: str) -> None:
    async with get_session() as db:
        call = await db.get(Call, call_id)
        if not call:
            raise CallNotFoundError(call_id)
        if call.receiver_id != user_id:
            raise CallValidationError("Only the receiver can reject this call")
        call.status = "rejected"
        call.ended_at = datetime.now(timezone.utc)


async def end_call(call_id: str, user_id: str) -> None:
    async with get_session() as db:
        call = await db.get(Call, call_id)
        if not call:
            raise CallNotFoundError(call_id)
        if user_id not in (call.caller_id, call.receiver_id):
            raise CallValidationError("Not a participant of this call")
        call.status = "ended"
        call.ended_at = datetime.now(timezone.utc)

        # Mark all participants left
        stmt = select(CallParticipant).where(CallParticipant.call_id == call_id)
        rows = (await db.execute(stmt)).scalars().all()
        for r in rows:
            if r.left_at is None:
                r.left_at = datetime.now(timezone.utc)


async def get_call(call_id: str, user_id: str) -> CallOut:
    async with get_session() as db:
        call = await db.get(Call, call_id)
        if not call:
            raise CallNotFoundError(call_id)
        if user_id not in (call.caller_id, call.receiver_id):
            raise CallValidationError("Not a participant of this call")

        stmt = select(CallParticipant).where(CallParticipant.call_id == call_id)
        parts = (await db.execute(stmt)).scalars().all()
        return CallOut(
            id=call.id,
            caller_id=call.caller_id,
            receiver_id=call.receiver_id,
            status=call.status,
            started_at=call.started_at,
            ended_at=call.ended_at,
            created_at=call.created_at,
            participants=[
                CallParticipantOut(
                    user_id=p.user_id,
                    source_language=p.source_language,
                    target_language=p.target_language,
                    joined_at=p.joined_at,
                    left_at=p.left_at,
                )
                for p in parts
            ],
        )


async def list_calls(user_id: str, limit: int = 50) -> list[CallOut]:
    async with get_session() as db:
        stmt = (
            select(Call)
            .where((Call.caller_id == user_id) | (Call.receiver_id == user_id))
            .order_by(Call.created_at.desc())
            .limit(limit)
        )
        calls = (await db.execute(stmt)).scalars().all()
        out: list[CallOut] = []
        for c in calls:
            stmt_p = select(CallParticipant).where(CallParticipant.call_id == c.id)
            parts = (await db.execute(stmt_p)).scalars().all()
            out.append(
                CallOut(
                    id=c.id,
                    caller_id=c.caller_id,
                    receiver_id=c.receiver_id,
                    status=c.status,
                    started_at=c.started_at,
                    ended_at=c.ended_at,
                    created_at=c.created_at,
                    participants=[
                        CallParticipantOut(
                            user_id=p.user_id,
                            source_language=p.source_language,
                            target_language=p.target_language,
                            joined_at=p.joined_at,
                            left_at=p.left_at,
                        )
                        for p in parts
                    ],
                )
            )
        return out


async def record_usage(
    user_id: str,
    call_id: str,
    audio_seconds: float,
    translation_seconds: float,
) -> None:
    async with get_session() as db:
        db.add(
            Usage(
                user_id=user_id,
                call_id=call_id,
                audio_seconds=audio_seconds,
                translation_seconds=translation_seconds,
            )
        )
