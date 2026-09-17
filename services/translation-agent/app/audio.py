"""Audio helpers — sample-rate conversion, frame chunking, PCM normalization.

All audio inside the agent is mono PCM float32 at AUDIO_SAMPLE_RATE (default 24 kHz),
matching the OpenAI Realtime API requirement. LiveKit delivers 16-bit PCM frames
at the room's sample rate (typically 48 kHz) — we downmix/convert here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import numpy as np

from app.config import settings


SAMPLE_RATE = settings.audio_sample_rate  # 24000 — Realtime API native rate
CHANNELS = settings.audio_channels
BYTES_PER_SAMPLE = 2  # int16 PCM


def pcm16_to_float32(pcm: bytes, channels: int = 1) -> np.ndarray:
    """Convert int16 PCM bytes to float32 in [-1, 1]."""
    if not pcm:
        return np.zeros(0, dtype=np.float32)
    arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        # Downmix to mono
        arr = arr.reshape(-1, channels).mean(axis=1)
    return arr


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    """Convert float32 in [-1, 1] to int16 PCM bytes."""
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype(np.int16).tobytes()


def resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Simple linear resampling — sufficient for speech (no anti-alias filter)."""
    if src_rate == dst_rate or len(samples) == 0:
        return samples
    ratio = dst_rate / src_rate
    n_out = max(1, int(len(samples) * ratio))
    idx = np.linspace(0, len(samples) - 1, n_out)
    return np.interp(idx, np.arange(len(samples)), samples).astype(np.float32)


@dataclass
class AudioFrame:
    """A normalized PCM float32 frame, mono, at SAMPLE_RATE Hz."""
    samples: np.ndarray
    sample_rate: int = SAMPLE_RATE
    generation_id: int = 0
    timestamp_ms: float = 0.0

    @property
    def duration_ms(self) -> float:
        return (len(self.samples) / self.sample_rate) * 1000.0


async def chunked(iter: AsyncIterator[AudioFrame], target_ms: int = 100) -> AsyncIterator[AudioFrame]:
    """Coalesce small frames into ~target_ms chunks for downstream consumption."""
    buffer: list[np.ndarray] = []
    buffered_samples = 0
    target_samples = int(SAMPLE_RATE * target_ms / 1000)
    async for frame in iter:
        buffer.append(frame.samples)
        buffered_samples += len(frame.samples)
        if buffered_samples >= target_samples:
            merged = np.concatenate(buffer)
            yield AudioFrame(
                samples=merged[:target_samples],
                sample_rate=SAMPLE_RATE,
                generation_id=frame.generation_id,
            )
            if len(merged) > target_samples:
                buffer = [merged[target_samples:]]
                buffered_samples = len(merged) - target_samples
            else:
                buffer = []
                buffered_samples = 0
    if buffer:
        yield AudioFrame(
            samples=np.concatenate(buffer),
            sample_rate=SAMPLE_RATE,
            generation_id=0,
        )


def ms_to_samples(ms: int, rate: int = SAMPLE_RATE) -> int:
    return int(rate * ms / 1000)
