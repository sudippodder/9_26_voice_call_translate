"""Usage / metrics ingestion routes — agent reports usage; users read their own."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import settings
from app.db.models import Usage
from app.db.session import get_db
from app.models.schemas import UsageOut, UserOut

router = APIRouter()


@router.get("/api/v1/usage", response_model=List[UsageOut])
async def list_my_usage(
    user: UserOut = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> List[UsageOut]:
    stmt = (
        select(Usage)
        .where(Usage.user_id == user.id)
        .order_by(Usage.created_at.desc())
        .limit(100)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        UsageOut(
            user_id=r.user_id,
            call_id=r.call_id,
            audio_seconds=r.audio_seconds,
            translation_seconds=r.translation_seconds,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Internal endpoint — only callable by the translation agent with a shared
# secret. Used to push usage at end-of-call. **Not** on the realtime audio path.
# ---------------------------------------------------------------------------
@router.post("/api/v1/internal/usage", status_code=201)
async def post_usage(
    payload: dict,
    x_internal_secret: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    expected = settings.livekit_api_secret  # reuse as a shared internal secret
    if not expected or x_internal_secret != expected:
        raise HTTPException(status_code=403, detail="forbidden")
    db.add(
        Usage(
            user_id=payload["user_id"],
            call_id=payload.get("call_id"),
            audio_seconds=float(payload.get("audio_seconds", 0.0)),
            translation_seconds=float(payload.get("translation_seconds", 0.0)),
        )
    )
    await db.commit()
    return {"ok": True}
