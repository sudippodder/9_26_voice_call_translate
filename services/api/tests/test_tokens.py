"""Tests for LiveKit token generation."""
import pytest

from app.config import settings


def test_token_generation():
    # Override settings to use known test values
    settings.livekit_url = "wss://test.livekit.cloud"
    settings.livekit_api_key = "test-key"
    settings.livekit_api_secret = "test-secret-xxxxxxxxxxxxxxxxxxxxxxxx"
    from app.livekit import create_participant_token
    tok = create_participant_token(call_id="call_abc", user_id="user_123")
    assert tok.url == "wss://test.livekit.cloud"
    assert tok.room_name == "call_abc"
    assert isinstance(tok.token, str)
    assert tok.token.count(".") == 2  # JWT


def test_agent_token_generation():
    settings.livekit_url = "wss://test.livekit.cloud"
    settings.livekit_api_key = "test-key"
    settings.livekit_api_secret = "test-secret-xxxxxxxxxxxxxxxxxxxxxxxx"
    from app.livekit import create_agent_token
    tok = create_agent_token("call_xyz")
    assert tok.room_name == "call_xyz"
    assert tok.token.count(".") == 2
