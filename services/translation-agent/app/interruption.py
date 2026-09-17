"""Interruption / barge-in controller + generation-ID bookkeeping.

Sequence on user speech-after-silence:

  1. VAD fires SPEECH_STARTED → InterruptController.barge_in()
  2. barge_in() atomically increments `generation_id`
  3. PlaybackQueue discards any chunk whose generation_id < current
  4. translator.interrupt() cancels the in-flight AI response
  5. New audio now flows under the new generation_id

This guarantees that stale translated audio (already en route from the AI
server when the user interrupted) is silently discarded rather than played.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional


class GenerationId:
    """Monotonic counter, bumped on every interruption."""

    def __init__(self) -> None:
        self._value = 0
        self._lock = asyncio.Lock()

    @property
    def value(self) -> int:
        return self._value

    async def bump(self) -> int:
        async with self._lock:
            self._value += 1
            return self._value

    def is_current(self, gen: int) -> bool:
        return gen == self._value


@dataclass
class InterruptCallbacks:
    on_barge_in: Optional[Callable[[], "asyncio.Future"]] = None


class InterruptController:
    """Coordinates interruption across translator + playback queue."""

    def __init__(
        self,
        gen_id: GenerationId,
        playback_queue: "PlaybackQueue",  # noqa: F821 — forward ref
        translator,  # RealtimeTranslator
    ):
        self.gen_id = gen_id
        self.playback = playback_queue
        self.translator = translator
        self._lock = asyncio.Lock()
        self._barge_in_count = 0

    @property
    def barge_in_count(self) -> int:
        return self._barge_in_count

    async def barge_in(self) -> int:
        """Atomically: bump gen, clear queue, cancel AI."""
        async with self._lock:
            new_gen = await self.gen_id.bump()
            self._barge_in_count += 1
            # 1) Clear the playback queue — anything still queued is stale.
            await self.playback.clear()
            # 2) Tell the AI to cancel its in-flight response.
            await self.translator.interrupt()
            # 3) Update translator's notion of "current generation" so any
            #    audio that arrives AFTER this point but is tagged with the
            #    old gen id gets discarded.
            self.translator.set_generation_id(new_gen)
            return new_gen
