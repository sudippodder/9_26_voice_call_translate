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

    # Provider selection: "openai" or "gemini"
    translator_provider: str = "openai"

    # OpenAI Realtime API
    openai_api_key: str = ""
    # MUST be a bidirectional realtime model. Options (verify at
    # https://platform.openai.com/docs/guides/realtime):
    #   - gpt-4o-realtime-preview             (recommended — full quality)
    #   - gpt-4o-realtime-preview-2024-12-17   (pinned version)
    # Do NOT use gpt-4o-mini-realtime-preview — text-in/audio-out only.
    realtime_translation_model: str = "gpt-4o-realtime-preview"
    realtime_translation_voice: str = "alloy"
    openai_realtime_base_url: str = "wss://api.openai.com/v1/realtime"

    # Gemini Live API
    gemini_api_key: str = ""
    # Gemini Live model — must be a "Live" variant that supports bidirectional audio.
    # Use /api/v1/test/list-gemini-models to see what your key can access.
    # Common valid names (your key may have a different subset):
    #   - gemini-2.5-flash-native-audio-latest   (recommended — has audio out)
    #   - gemini-2.5-flash-preview-native-audio-dialog
    #   - gemini-2.0-flash-live-001              (older)
    #   - gemini-2.0-flash-exp                    (experimental)
    gemini_model: str = "gemini-2.5-flash-native-audio-latest"
    # Gemini voice options: Puck, Charon, Fenrir, Aoede, Leda, Orus
    gemini_voice: str = "Aoede"

    # VAD
    vad_prefix_padding_ms: int = 250
    vad_silence_duration_ms: int = 350

    # Audio
    # Note: OpenAI uses 24kHz. Gemini Live uses 16kHz. The audio.py module
    # resamples between rates as needed.
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
