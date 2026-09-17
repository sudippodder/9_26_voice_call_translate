"""Language registry — extend by adding entries to LANGUAGES."""
from __future__ import annotations

from typing import Dict, List

from pydantic import BaseModel


class Language(BaseModel):
    code: str  # ISO 639-1
    name: str  # English name
    native_name: str  # Endonym


# V1 supported languages. Adding a new language only requires appending here.
LANGUAGES: List[Language] = [
    Language(code="en", name="English", native_name="English"),
    Language(code="hi", name="Hindi", native_name="हिन्दी"),
    Language(code="bn", name="Bengali", native_name="বাংলা"),
]

LANGUAGES_BY_CODE: Dict[str, Language] = {l.code: l for l in LANGUAGES}


def get_language(code: str) -> Language | None:
    return LANGUAGES_BY_CODE.get(code)


def is_supported(code: str) -> bool:
    return code in LANGUAGES_BY_CODE
