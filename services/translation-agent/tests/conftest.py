"""Pytest config for agent tests."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LIVEKIT_URL", "wss://test")
os.environ.setdefault("LIVEKIT_API_KEY", "k")
os.environ.setdefault("LIVEKIT_API_SECRET", "s")
os.environ.setdefault("AUDIO_SAMPLE_RATE", "24000")
