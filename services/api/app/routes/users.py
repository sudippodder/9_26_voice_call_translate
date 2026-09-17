"""User routes — search by email, get/set language preferences."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.db.models import User, UserLanguagePreference
from app.db.session import get_db
from app.models.schemas import LanguagePreference, UserOut

router = APIRouter()


@router.get("/api/v1/users/search", response_model=list[UserOut])
async def search_users(
    q: str = Query(..., min_length=2, description="Search by email or display name"),
    user: UserOut = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[UserOut]:
    """Search users by email or display name. Excludes self."""
    pattern = f"%{q}%"
    stmt = select(User).where(
        or_(User.email.ilike(pattern), User.display_name.ilike(pattern)),
        User.id != user.id,
    ).limit(20)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        UserOut(id=r.id, email=r.email, display_name=r.display_name, created_at=r.created_at)
        for r in rows
    ]


@router.get("/api/v1/users/{user_id}", response_model=UserOut)
async def get_user(
    user_id: str,
    user: UserOut = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    """Get a user's public profile."""
    if user_id == user.id:
        return user
    row = await db.get(User, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut(id=row.id, email=row.email, display_name=row.display_name, created_at=row.created_at)


@router.get("/api/v1/me/preferences", response_model=LanguagePreference)
async def get_preferences(
    user: UserOut = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LanguagePreference:
    stmt = select(UserLanguagePreference).where(
        UserLanguagePreference.user_id == user.id
    ).order_by(UserLanguagePreference.updated_at.desc()).limit(1)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row:
        return LanguagePreference(speak=row.speak, hear=row.hear)
    # Defaults
    return LanguagePreference(speak="en", hear="hi")


@router.put("/api/v1/me/preferences", response_model=LanguagePreference)
async def set_preferences(
    payload: LanguagePreference,
    user: UserOut = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LanguagePreference:
    from app.languages import is_supported
    if not (is_supported(payload.speak) and is_supported(payload.hear)):
        raise HTTPException(status_code=400, detail="Unsupported language code")
    if payload.speak == payload.hear:
        raise HTTPException(status_code=400, detail="speak and hear must differ")

    stmt = select(UserLanguagePreference).where(
        UserLanguagePreference.user_id == user.id
    ).order_by(UserLanguagePreference.updated_at.desc()).limit(1)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row:
        row.speak = payload.speak
        row.hear = payload.hear
    else:
        row = UserLanguagePreference(
            user_id=user.id, speak=payload.speak, hear=payload.hear
        )
        db.add(row)
    await db.commit()
    return LanguagePreference(speak=row.speak, hear=row.hear)


@router.get("/api/v1/calls/incoming", response_model=list[dict])
async def list_incoming_calls(
    user: UserOut = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Get pending calls addressed to the current user (for the receiver to poll)."""
    from datetime import datetime, timezone, timedelta
    from app.db.models import Call
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=45)
    stmt = (
        select(Call)
        .where(
            Call.receiver_id == user.id,
            Call.status.in_(["pending", "ringing"]),
            Call.created_at >= cutoff,
        )
        .order_by(Call.created_at.desc())
        .limit(10)
    )
    calls = (await db.execute(stmt)).scalars().all()
    # Include caller display info
    out: list[dict] = []
    for c in calls:
        caller = await db.get(User, c.caller_id)
        # Also fetch participants for language pair info
        from app.db.models import CallParticipant
        stmt_p = select(CallParticipant).where(
            CallParticipant.call_id == c.id,
            CallParticipant.user_id == c.caller_id,
        )
        caller_p = (await db.execute(stmt_p)).scalar_one_or_none()
        out.append({
            "call_id": c.id,
            "caller_id": c.caller_id,
            "caller_name": caller.display_name if caller else c.caller_id,
            "caller_email": caller.email if caller else None,
            "caller_speak": caller_p.source_language if caller_p else None,
            "caller_hear": caller_p.target_language if caller_p else None,
            "status": c.status,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return out
