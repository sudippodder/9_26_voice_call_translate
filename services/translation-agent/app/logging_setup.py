"""Structured logging — never logs raw audio or secrets."""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.config import settings


_SECRET_KEYS = {
    "openai_api_key",
    "livekit_api_secret",
    "livekit_api_key",
    "token",
    "authorization",
    "api_key",
}


def _redact(_: logging.Logger, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict.keys()):
        if key.lower() in _SECRET_KEYS:
            event_dict[key] = "***"
    return event_dict


def configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _redact,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
