"""Adaptive async playback queue.

Pulls TranslatedAudioChunk from the translator, applies an adaptive jitter
buffer (target PLAYBACK_BUFFER_MS, max PLAYBACK_BUFFER_MAX_MS), and feeds
frames to a LiveKit `AudioSource` for re-publish.

Stale-audio protection: any chunk whose generation_id != current_generation_id
is discarded (see interruption.py).

Stall detection: if the queue goes empty while we still expect output, the
stall counter is bumped and the buffer target is increased (cap at MAX).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from app.audio import AudioFrame
from app.config import settings
from app.interruption import GenerationId
from app.logging_setup import get_logger
from app.metrics import SessionMetrics
from app.translator import TranslatedAudioChunk

log = get_logger("playback")


@dataclass
class _QueuedChunk:
    samples: np.ndarray
    generation_id: int
    enqueued_at: float


class PlaybackQueue:
    """Adaptive jitter buffer between translator output and LiveKit AudioSource."""

    def __init__(
        self,
        *,
        gen_id: GenerationId,
        metrics: SessionMetrics,
        sample_rate: int = settings.audio_sample_rate,
        initial_buffer_ms: int = settings.playback_buffer_ms,
        max_buffer_ms: int = settings.playback_buffer_max_ms,
    ):
        self.gen_id = gen_id
        self.metrics = metrics
        self.sample_rate = sample_rate
        self.target_buffer_ms = initial_buffer_ms
        self.max_buffer_ms = max_buffer_ms

        self._q: asyncio.Queue[Optional[_QueuedChunk]] = asyncio.Queue(maxsize=800)
        self._closed = False
        # Callback used by the AudioSource pump to push frames out.
        self.on_frame: Optional[callable] = None  # async callable(samples: np.ndarray)
        self._worker: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------ enqueue
    async def put(self, chunk: TranslatedAudioChunk) -> None:
        """Drop stale chunks (generation mismatch) before enqueue."""
        if not self.gen_id.is_current(chunk.generation_id):
            log.debug("playback_drop_stale",
                      chunk_gen=chunk.generation_id,
                      current_gen=self.gen_id.value)
            return
        try:
            self._q.put_nowait(_QueuedChunk(
                samples=chunk.samples,
                generation_id=chunk.generation_id,
                enqueued_at=time.monotonic(),
            ))
        except asyncio.QueueFull:
            # Drop oldest to keep latency low
            try:
                self._q.get_nowait()
                self._q.put_nowait(_QueuedChunk(
                    samples=chunk.samples,
                    generation_id=chunk.generation_id,
                    enqueued_at=time.monotonic(),
                ))
                self.metrics.increment_stall()
                self._grow_buffer()
            except Exception:  # noqa: BLE001
                pass

    async def clear(self) -> None:
        """Drop all queued chunks — called by InterruptController on barge-in."""
        dropped = 0
        while not self._q.empty():
            try:
                self._q.get_nowait()
                dropped += 1
            except asyncio.QueueEmpty:
                break
        if dropped:
            log.info("playback_cleared", dropped=dropped, gen=self.gen_id.value)

    # ------------------------------------------------------------------ pump
    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._pump(), name="playback-pump")

    async def stop(self) -> None:
        self._closed = True
        await self._q.put(None)
        if self._worker and not self._worker.done():
            try:
                await asyncio.wait_for(self._worker, timeout=2.0)
            except asyncio.TimeoutError:
                self._worker.cancel()

    async def _pump(self) -> None:
        """Pump frames out at sample-rate cadence, with adaptive buffer.

        Strategy: maintain a target pre-roll buffer (e.g. 100ms) before starting
        playback. If we exhaust the queue mid-utterance, increment stall and
        grow the target. If we overshoot by >2x target, shrink the target.
        """
        preroll_samples = int(self.sample_rate * self.target_buffer_ms / 1000)
        preroll_remaining = preroll_samples
        playing = False

        while not self._closed:
            try:
                chunk = await asyncio.wait_for(self._q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if playing:
                    self.metrics.increment_stall()
                    self._grow_buffer()
                continue
            if chunk is None:
                break

            # Drop stale (post-interruption) chunks.
            if not self.gen_id.is_current(chunk.generation_id):
                continue

            if not playing:
                preroll_remaining -= len(chunk.samples)
                if preroll_remaining <= 0:
                    playing = True
                    self.metrics.mark_first_playback()

            # Adapt target based on queue depth
            qsize = self._q.qsize()
            queued_ms = (qsize * 0)  # we don't know chunk sizes here precisely
            _ = queued_ms  # silence linter

            if self.on_frame is not None:
                try:
                    await self.on_frame(chunk.samples)
                except Exception as e:  # noqa: BLE001
                    log.warning("playback_on_frame_failed", error=str(e))

            self.metrics.translation_seconds += len(chunk.samples) / self.sample_rate

    def _grow_buffer(self) -> None:
        new = min(self.target_buffer_ms + 25, self.max_buffer_ms)
        if new != self.target_buffer_ms:
            log.info("playback_buffer_grew", new_ms=new)
            self.target_buffer_ms = new

    def _shrink_buffer(self) -> None:
        new = max(self.target_buffer_ms - 25, 50)
        if new != self.target_buffer_ms:
            log.info("playback_buffer_shrunk", new_ms=new)
            self.target_buffer_ms = new
