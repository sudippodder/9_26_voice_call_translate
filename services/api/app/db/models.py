"""SQLAlchemy ORM models for PostgreSQL/Supabase."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[Optional[str]] = mapped_column(Text, unique=True, nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    preferences: Mapped[list["UserLanguagePreference"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserLanguagePreference(Base):
    __tablename__ = "user_language_preferences"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    speak: Mapped[str] = mapped_column(String(8))
    hear: Mapped[str] = mapped_column(String(8))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="preferences")


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: f"call_{_uuid()}")
    caller_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    receiver_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    # pending → ringing → active → ended
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    participants: Mapped[list["CallParticipant"]] = relationship(
        back_populates="call", cascade="all, delete-orphan"
    )


class CallParticipant(Base):
    __tablename__ = "call_participants"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_language: Mapped[str] = mapped_column(String(8))
    target_language: Mapped[str] = mapped_column(String(8))
    joined_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    left_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    call: Mapped["Call"] = relationship(back_populates="participants")


class Usage(Base):
    __tablename__ = "usage"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    call_id: Mapped[Optional[str]] = mapped_column(ForeignKey("calls.id"), nullable=True, index=True)
    audio_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    translation_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class CallQualityMetric(Base):
    __tablename__ = "call_quality_metrics"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    call_id: Mapped[str] = mapped_column(ForeignKey("calls.id"), index=True)
    participant_id: Mapped[str] = mapped_column(String, index=True)
    metric_name: Mapped[str] = mapped_column(String(64))
    metric_value: Mapped[float] = mapped_column(Float)
    # Optional JSON-ish payload stored as text for portability across pg versions
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
