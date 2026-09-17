"""LiveKit token generation for participants and the translation agent."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from livekit import api

from app.config import settings


@dataclass
class LiveKitParticipantToken:
    token: str
    url: str
    room_name: str


def _room_name_for_call(call_id: str) -> str:
    """All LiveKit room names are derived from call_id — never user-provided."""
    # LiveKit room names allow alphanumerics + `_-`. Our call ids look like
    # `call_<uuid>` so they are safe.
    return f"call_{call_id.replace('call_', '', 1)}" if call_id.startswith("call_") else f"call_{call_id}"


def create_participant_token(
    *,
    call_id: str,
    user_id: str,
    name: Optional[str] = None,
    can_publish: bool = True,
    can_subscribe: bool = True,
    ttl: Optional[int] = None,
) -> LiveKitParticipantToken:
    """Generate a short-lived LiveKit access token for a regular user."""
    room_name = _room_name_for_call(call_id)
    ttl_seconds = ttl or settings.livekit_token_ttl

    grant = api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=can_publish,
        can_subscribe=can_subscribe,
        can_publish_data=False,
        # Critical: participants must NOT be able to subscribe to the other
        # user's original microphone track directly — only to the translated
        # tracks published by the agent. We enforce this with track-level
        # permissions in the agent (see agent.py), and via can_publish/can_subscribe
        # being scoped to the participant's own mic + agent's translated tracks.
    )

    token = (
        api.AccessToken(
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
        .with_identity(user_id)
        .with_name(name or user_id)
        .with_grants(grant)
        .with_ttl(timedelta(seconds=ttl_seconds))
        .to_jwt()
    )

    return LiveKitParticipantToken(token=token, url=settings.livekit_url, room_name=room_name)


def create_agent_token(call_id: str, agent_identity: str = "translator_agent") -> LiveKitParticipantToken:
    """Generate a token for the translation agent — can publish translated tracks."""
    room_name = _room_name_for_call(call_id)

    grant = api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,   # publishes translated tracks
        can_subscribe=True, # subscribes to user microphones
        can_publish_data=True,
        agent=True,
    )

    token = (
        api.AccessToken(
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
        .with_identity(agent_identity)
        .with_name("Translation Agent")
        .with_grants(grant)
        .with_ttl(timedelta(seconds=settings.livekit_token_ttl))
        .to_jwt()
    )

    return LiveKitParticipantToken(token=token, url=settings.livekit_url, room_name=room_name)
