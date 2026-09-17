"""Language code → display name map shared by the agent."""
from __future__ import annotations

LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "bn": "Bengali",
}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)
