"""Translation Agent configuration."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    log_level: str = "INFO"

    # LiveKit
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    # Realtime AI
    openai_api_key: str = ""
    realtime_translation_model: str = "gpt-4o-mini-realtime-preview"
    realtime_translation_voice: str = "alloy"
    openai_realtime_base_url: str = "wss://api.openai.com/v1/realtime"

    # VAD
    vad_prefix_padding_ms: int = 250
    vad_silence_duration_ms: int = 350

    # Audio
    audio_sample_rate: int = 24000
    audio_channels: int = 1
    playback_buffer_ms: int = 100
    playback_buffer_max_ms: int = 400

    # Redis (used for cross-agent coordination on shutdown)
    redis_url: str = "redis://localhost:6379/0"

    # API (for usage reporting)
    api_internal_secret: str = ""
    api_url: str = "http://localhost:8000"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
