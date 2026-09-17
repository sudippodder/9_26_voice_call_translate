"""Pydantic request/response models for the API."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------
class UserOut(BaseModel):
    id: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    created_at: Optional[datetime] = None


class LanguagePreference(BaseModel):
    speak: str  # ISO code user speaks
    hear: str  # ISO code user wants to hear


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------
class CallCreate(BaseModel):
    callee_id: str
    source_language: str  # caller speaks
    target_language: str  # caller wants to hear


class CallCreateResponse(BaseModel):
    call_id: str
    room_name: str
    livekit_url: str
    participant_token: str
    # The receiver also needs their own language pair — derived server-side
    # from their saved preferences or from the inverse of the caller's choice.


class CallParticipantOut(BaseModel):
    user_id: str
    source_language: str
    target_language: str
    joined_at: Optional[datetime] = None
    left_at: Optional[datetime] = None


class CallOut(BaseModel):
    id: str
    caller_id: str
    receiver_id: str
    status: str
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    created_at: datetime
    participants: List[CallParticipantOut] = Field(default_factory=list)


class CallListResponse(BaseModel):
    calls: List[CallOut]


class CallAcceptResponse(BaseModel):
    call_id: str
    room_name: str
    livekit_url: str
    participant_token: str


# ---------------------------------------------------------------------------
# Health & diagnostics
# ---------------------------------------------------------------------------
class HealthOut(BaseModel):
    status: str
    version: str
    app_env: str


class LanguagesOut(BaseModel):
    languages: List[dict]


# ---------------------------------------------------------------------------
# Usage / metrics
# ---------------------------------------------------------------------------
class UsageOut(BaseModel):
    user_id: str
    call_id: Optional[str] = None
    audio_seconds: float = 0.0
    translation_seconds: float = 0.0
    created_at: datetime


# ---------------------------------------------------------------------------
# Auth (dev mode)
# ---------------------------------------------------------------------------
class DevLoginIn(BaseModel):
    email: str
    display_name: Optional[str] = None


class DevLoginOut(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    user: UserOut
