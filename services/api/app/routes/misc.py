"""Health, languages, dev login, /me."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.auth import get_current_user, issue_dev_jwt
from app.config import settings
from app.db.models import User, UserLanguagePreference
from app.db.session import get_session
from app.languages import LANGUAGES
from app.models.schemas import (
    DevLoginIn,
    DevLoginOut,
    HealthOut,
    LanguagesOut,
    UserOut,
)

router = APIRouter()


@router.get("/health", response_model=HealthOut)
async def health() -> HealthOut:
    return HealthOut(status="ok", version="1.0.0", app_env=settings.app_env)


@router.get("/api/v1/languages", response_model=LanguagesOut)
async def list_languages() -> LanguagesOut:
    return LanguagesOut(languages=[l.model_dump() for l in LANGUAGES])


@router.post("/api/v1/auth/dev-login", response_model=DevLoginOut)
async def dev_login(payload: DevLoginIn) -> DevLoginOut:
    """DEV ONLY — mint a JWT for any email.

    Idempotent: if a user with that email already exists, reuse them.
    This way two different people can sign in with different emails and
    actually find each other via the user-search endpoint.

    If the database is unavailable (e.g., in tests / early dev), we still
    mint a JWT but skip persistence — login works, but search won't find
    the user until DB is up.
    """
    user_id = f"user_{uuid.uuid4()}"
    try:
        async with get_session() as db:
            stmt = select(User).where(User.email == payload.email)
            existing = (await db.execute(stmt)).scalar_one_or_none()
            if existing:
                user_id = existing.id
                # Update display name if provided
                if payload.display_name and existing.display_name != payload.display_name:
                    existing.display_name = payload.display_name
            else:
                new_user = User(
                    id=user_id,
                    email=payload.email,
                    display_name=payload.display_name or payload.email.split("@")[0],
                )
                db.add(new_user)
                # Also create a default language preference so the user can be a callee
                db.add(UserLanguagePreference(
                    user_id=user_id, speak="en", hear="hi",
                ))
                await db.flush()
    except Exception as e:  # noqa: BLE001
        # DB not available — fall back to stateless JWT
        # Use a stable ID derived from email so the same email always yields the same ID
        import hashlib
        user_id = f"user_{hashlib.sha256(payload.email.encode()).hexdigest()[:16]}"

    token = issue_dev_jwt(
        user_id=user_id, email=payload.email, display_name=payload.display_name
    )
    return DevLoginOut(
        access_token=token,
        user=UserOut(id=user_id, email=payload.email, display_name=payload.display_name),
    )


@router.get("/api/v1/me", response_model=UserOut)
async def me(user: UserOut = Depends(get_current_user)) -> UserOut:
    return user
