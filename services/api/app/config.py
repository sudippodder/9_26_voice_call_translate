"""Application configuration loaded from environment variables."""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Database
    database_url: str = "postgresql+asyncpg://vtranslate:vtranslate@localhost:5432/voice_translator"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Auth
    auth_provider: str = "dev"  # "dev" or "supabase"
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # LiveKit
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    livekit_token_ttl: int = 3600

    # AI
    openai_api_key: str = ""
    realtime_translation_model: str = "gpt-realtime-translate"
    realtime_translation_voice: str = "alloy"
    openai_realtime_base_url: str = "wss://api.openai.com/v1/realtime"

    # VAD / audio
    vad_prefix_padding_ms: int = 250
    vad_silence_duration_ms: int = 350
    playback_buffer_ms: int = 100
    playback_buffer_max_ms: int = 400
    audio_sample_rate: int = 24000
    audio_channels: int = 1

    # Stress test
    stress_test_enabled: bool = False
    stress_test_concurrency: int = 10

    @field_validator("cors_origins")
    @classmethod
    def _strip_cors(cls, v: str) -> str:
        return ",".join([o.strip() for o in v.split(",") if o.strip()])

    @property
    def cors_origins_list(self) -> List[str]:
        return self.cors_origins.split(",")

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
