"""Voice Activity Detection — server-side, low-latency.

Uses a simple energy-based VAD with adaptive noise floor as a robust default
that requires no external model weights. The interface mirrors Silero's
`VOICE_ACTIVITY` interface used by livekit-plugins-silero so the VAD can be
swapped for Silero without changing the agent.

Emits three events:
- SpeechStarted (after `prefix_padding_ms` of speech-like energy)
- SpeechFrame   (continuous while speech is ongoing)
- SpeechEnded   (after `silence_duration_ms` of below-threshold energy)

Tuning: VAD_PREFIX_PADDING_MS and VAD_SILENCE_DURATION_MS (env vars).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator, Optional

import numpy as np

from app.config import settings


class VadEventKind(Enum):
    SPEECH_STARTED = "speech_started"
    SPEECH_FRAME = "speech_frame"
    SPEECH_ENDED = "speech_ended"


@dataclass
class VadEvent:
    kind: VadEventKind
    samples: np.ndarray  # audio that triggered the event (float32 mono)
    timestamp_ms: float


class StreamingVAD:
    """Energy-based streaming VAD with adaptive noise floor.

    Output is consumed asynchronously via `events()`.
    """

    def __init__(
        self,
        *,
        prefix_padding_ms: Optional[int] = None,
        silence_duration_ms: Optional[int] = None,
        energy_threshold: float = 0.012,  # tuned for normalized speech
        sample_rate: int = settings.audio_sample_rate,
    ):
        self.prefix_padding_ms = prefix_padding_ms or settings.vad_prefix_padding_ms
        self.silence_duration_ms = silence_duration_ms or settings.vad_silence_duration_ms
        self.energy_threshold = energy_threshold
        self.sample_rate = sample_rate

        self._q: asyncio.Queue[Optional[VadEvent]] = asyncio.Queue(maxsize=500)
        self._speaking = False
        self._last_speech_ts: Optional[float] = None
        self._speech_start_ts: Optional[float] = None
        self._noise_floor = 0.005
        self._closed = False

    # ------------------------------------------------------------------ push
    def push(self, samples: np.ndarray) -> None:
        """Process one chunk of audio."""
        if self._closed or len(samples) == 0:
            return
        now_ms = time.monotonic() * 1000.0
        rms = float(np.sqrt(np.mean(np.square(samples))))
        # Adaptive noise floor — only adjusts DOWN when not speaking
        if not self._speaking and rms > self._noise_floor * 0.5:
            self._noise_floor = 0.9 * self._noise_floor + 0.1 * rms
        threshold = max(self.energy_threshold, self._noise_floor * 2.5)

        is_speech = rms > threshold

        if is_speech:
            if not self._speaking:
                # Only flip state once we've held speech for prefix_padding_ms
                if self._speech_start_ts is None:
                    self._speech_start_ts = now_ms
                elif (now_ms - self._speech_start_ts) >= self.prefix_padding_ms:
                    self._speaking = True
                    self._last_speech_ts = now_ms
                    self._put(VadEvent(VadEventKind.SPEECH_STARTED, samples, now_ms))
            else:
                self._last_speech_ts = now_ms
                self._put(VadEvent(VadEventKind.SPEECH_FRAME, samples, now_ms))
        else:
            if self._speaking:
                silence_ms = now_ms - (self._last_speech_ts or now_ms)
                if silence_ms >= self.silence_duration_ms:
                    self._speaking = False
                    self._speech_start_ts = None
                    self._put(VadEvent(VadEventKind.SPEECH_ENDED, samples, now_ms))

    def _put(self, ev: VadEvent) -> None:
        try:
            self._q.put_nowait(ev)
        except asyncio.QueueFull:
            try:
                self._q.get_nowait()
                self._q.put_nowait(ev)
            except Exception:  # noqa: BLE001
                pass

    async def events(self) -> AsyncIterator[VadEvent]:
        while not self._closed:
            ev = await self._q.get()
            if ev is None:
                break
            yield ev

    def close(self) -> None:
        self._closed = True
        try:
            self._q.put_nowait(None)
        except Exception:  # noqa: BLE001
            pass
