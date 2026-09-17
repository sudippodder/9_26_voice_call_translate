"""FastAPI entrypoint.

The API is **never** on the realtime audio path — it only:
- issues LiveKit tokens
- manages call metadata
- stores usage/metrics

Audio flows: Browser → LiveKit → Translation Agent → LiveKit → Browser.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.logging_setup import configure_logging, get_logger
from app.routes import calls as calls_routes
from app.routes import misc as misc_routes
from app.routes import usage as usage_routes
from app.routes import users as users_routes


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log = get_logger("api")
    log.info(
        "api_starting",
        env=settings.app_env,
        auth_provider=settings.auth_provider,
        livekit_configured=bool(settings.livekit_url and settings.livekit_api_secret),
    )
    # In dev, auto-create tables if missing. Production uses migrations.
    if settings.is_dev:
        try:
            from app.db.session import init_db
            await init_db()
            log.info("db_init_done")
        except Exception as e:  # noqa: BLE001
            log.warning("db_init_failed", error=str(e))
    yield
    log.info("api_stopping")


app = FastAPI(
    title="Voice Translator API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=r"^https?://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(misc_routes.router)
app.include_router(users_routes.router)
app.include_router(calls_routes.router)
app.include_router(usage_routes.router)


@app.get("/")
async def root():
    return {"name": "voice-translator-api", "docs": "/docs", "health": "/health"}
