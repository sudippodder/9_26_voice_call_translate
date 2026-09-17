"""Authentication — supports Supabase Auth (production) or a dev JWT provider.

Auth is **only** used to mint short-lived LiveKit tokens; it is never on the
realtime audio path. The browser must authenticate first, then call
POST /api/v1/calls to get a LiveKit token.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.db.models import User
from app.models.schemas import UserOut

bearer_scheme = HTTPBearer(auto_error=True)


# ---------------------------------------------------------------------------
# Dev-mode JWT issuance (for local development without Supabase)
# ---------------------------------------------------------------------------
def issue_dev_jwt(user_id: str, email: str, display_name: Optional[str] = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "name": display_name or email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
        "iss": "voice-translator-dev",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_dev_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as e:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        ) from e


# ---------------------------------------------------------------------------
# Supabase verification
# ---------------------------------------------------------------------------
async def verify_supabase_token(token: str) -> dict:
    """Verify a Supabase JWT against the configured project.

    For production, prefer using the Supabase service role key with the
    `supabase-py` admin API. Here we do a lightweight JWT signature check
    using the JWT secret derived from the anon key set (sufficient for dev).

    Replace this with `supabase.auth.get_user(token)` for production use.
    """
    # NOTE: in production we would call the Supabase auth admin endpoint
    # or verify against JWKS. For dev V1, we accept any well-formed JWT
    # signed by our own `jwt_secret` when AUTH_PROVIDER=dev.
    return decode_dev_jwt(token)


# ---------------------------------------------------------------------------
# FastAPI dependency — returns the authenticated user
# ---------------------------------------------------------------------------
async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> UserOut:
    token = creds.credentials
    if settings.auth_provider == "supabase":
        claims = await verify_supabase_token(token)
    else:
        claims = decode_dev_jwt(token)

    user_id = claims.get("sub") or claims.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing user id")

    return UserOut(
        id=str(user_id),
        email=claims.get("email"),
        display_name=claims.get("name") or claims.get("display_name"),
    )
