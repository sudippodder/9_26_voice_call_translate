"""Pytest config for API tests."""
import asyncio
import os
import sys
from pathlib import Path

# Make `app` importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# Force test env vars — must override any inherited system env
os.environ["DATABASE_URL"] = "postgresql+asyncpg://vtranslate:vtranslate@localhost:5432/voice_translator_test"
os.environ["REDIS_URL"] = "redis://localhost:6379/15"
os.environ["AUTH_PROVIDER"] = "dev"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["LIVEKIT_URL"] = "wss://test.livekit.cloud"
os.environ["LIVEKIT_API_KEY"] = "test-key"
os.environ["LIVEKIT_API_SECRET"] = "test-secret-xxxxxxxxxxxxxxxxxxxxxxxxxx"
os.environ["APP_ENV"] = "test"


import pytest


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
