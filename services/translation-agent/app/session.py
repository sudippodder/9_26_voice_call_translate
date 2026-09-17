"""A single translation session (one direction: A→B or B→A).

Per call we instantiate TWO sessions, one for each direction.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from app.audio import pcm16_to_float32, resample_linear
from app.config import settings
from app.interruption import GenerationId, InterruptController
from app.logging_setup import get_logger
from app.metrics import SessionMetrics
from app.playback import PlaybackQueue
from app.translator import RealtimeTranslator, TranslatedAudioChunk, TranscriptEvent
from app.vad import StreamingVAD, VadEventKind

log = get_logger("session")


@dataclass
class TranslationSessionConfig:
    call_id: str
    participant_id: str  # the speaker (source of input audio)
    listener_id: str  # who hears translated audio
    source_language: str
    target_language: str
    input_track_sample_rate: int = 48000  # LiveKit's default
    input_track_channels: int = 1
    agent_identity: str = "translator_agent"


class TranslationSession:
    """Owns one direction of translation.

    Lifecycle:
    1. start() — open translator, start VAD, playback pump
    2. ingest_audio(pcm16_bytes) — push user mic frames (from LiveKit track subscription)
    3. handle_vad_events() — drive commit/interrupt based on VAD events
    4. handle_translator_output() — pump translator audio → playback queue → LiveKit
    5. handle_transcript() — emit transcript events (optional, non-blocking)
    6. stop() — clean shutdown
    """

    def __init__(
        self,
        cfg: TranslationSessionConfig,
        *,
        translator: Optional[RealtimeTranslator] = None,
        on_publish_frame: Optional[callable] = None,  # async(samples: np.ndarray) -> None
        on_transcript: Optional[callable] = None,  # async(TranscriptEvent) -> None
        on_input_transcript: Optional[callable] = None,  # async(TranscriptEvent) -> None
    ):
        self.cfg = cfg
        self.translator = translator or RealtimeTranslator(
            source_language=cfg.source_language,
            target_language=cfg.target_language,
        )
        self.on_publish_frame = on_publish_frame
        self.on_transcript = on_transcript
        self.on_input_transcript = on_input_transcript

        self.gen_id = GenerationId()
        self.metrics = SessionMetrics(
            call_id=cfg.call_id,
            participant_id=cfg.participant_id,
            source_language=cfg.source_language,
            target_language=cfg.target_language,
        )
        self.playback = PlaybackQueue(gen_id=self.gen_id, metrics=self.metrics)
        self.playback.on_frame = on_publish_frame

        self.vad = StreamingVAD()
        self.interrupt_ctl = InterruptController(
            gen_id=self.gen_id,
            playback_queue=self.playback,
            translator=self.translator,
        )

        self._tasks: list[asyncio.Task] = []
        self._closed = False
        self._current_speech_gen = 0  # gen id of the speech currently being translated

    # ------------------------------------------------------------------ start
    async def start(self) -> None:
        await self.translator.connect()
        # Initial generation = 1
        self._current_speech_gen = await self.gen_id.bump()
        self.translator.set_generation_id(self._current_speech_gen)
        log.info("session_started",
                 call_id=self.cfg.call_id,
                 participant_id=self.cfg.participant_id,
                 src=self.cfg.source_language,
                 tgt=self.cfg.target_language,
                 gen=self._current_speech_gen)

        self.playback.start()
        self._tasks = [
            asyncio.create_task(self._vad_loop(), name=f"vad-{self.cfg.participant_id}"),
            asyncio.create_task(self._translator_output_loop(), name=f"out-{self.cfg.participant_id}"),
            asyncio.create_task(self._transcript_loop(), name=f"trn-{self.cfg.participant_id}"),
            asyncio.create_task(self._input_transcript_loop(), name=f"itrn-{self.cfg.participant_id}"),
        ]

    # ------------------------------------------------------------------ audio ingest
    async def ingest_audio(self, pcm16: bytes, *, sample_rate: Optional[int] = None) -> None:
        """Called per LiveKit audio frame from the speaker's mic track.

        Converts to float32 mono at SAMPLE_RATE, then:
          - feeds VAD
          - feeds translator (continuous streaming, never per-sentence)
        """
        if self._closed:
            return
        src_rate = sample_rate or self.cfg.input_track_sample_rate
        # Convert to float32 mono
        samples = pcm16_to_float32(pcm16, channels=self.cfg.input_track_channels)
        if self.cfg.input_track_sample_rate != settings.audio_sample_rate:
            samples = resample_linear(samples, src_rate, settings.audio_sample_rate)

        # VAD push — synchronous, very fast (RMS computation)
        try:
            self.vad.push(samples)
        except Exception as e:  # noqa: BLE001
            log.warning("vad_push_failed", error=str(e))

        # Continuous streaming to translator — never wait for sentence end
        try:
            await self.translator.send_audio(samples)
        except Exception as e:  # noqa: BLE001
            log.warning("translator_send_failed", error=str(e))

        self.metrics.audio_seconds += len(samples) / settings.audio_sample_rate

    # ------------------------------------------------------------------ VAD loop
    async def _vad_loop(self) -> None:
        async for ev in self.vad.events():
            if ev.kind == VadEventKind.SPEECH_STARTED:
                self.metrics.mark_speech_start()
                # If we were mid-translation, that's a barge-in.
                if self._current_speech_gen > 0:
                    new_gen = await self.interrupt_ctl.barge_in()
                    self._current_speech_gen = new_gen
                    self.metrics.increment_interrupt()
                    self.metrics.bump_generation(new_gen)
                    log.info("session_interrupt",
                             call_id=self.cfg.call_id,
                             participant_id=self.cfg.participant_id,
                             new_gen=new_gen)
                else:
                    self._current_speech_gen = self.gen_id.value
            elif ev.kind == VadEventKind.SPEECH_ENDED:
                # Commit input and request response from AI.
                try:
                    await self.translator.commit_input()
                except Exception as e:  # noqa: BLE001
                    log.warning("commit_failed", error=str(e))

    # ------------------------------------------------------------------ translator output loop
    async def _translator_output_loop(self) -> None:
        async for chunk in self.translator.receive_audio():
            if self._closed:
                break
            self.metrics.mark_first_ai_audio()
            await self.playback.put(chunk)
            # Record latency sample (queue time + render time, approx)
            latency_ms = chunk.samples.shape[0] / settings.audio_sample_rate * 1000.0
            self.metrics.record_ai_latency(latency_ms)

    async def _transcript_loop(self) -> None:
        async for ev in self.translator.receive_transcript():
            if self._closed:
                break
            if self.on_transcript is not None:
                try:
                    await self.on_transcript(ev)
                except Exception as e:  # noqa: BLE001
                    log.warning("transcript_callback_failed", error=str(e))

    async def _input_transcript_loop(self) -> None:
        async for ev in self.translator.receive_input_transcript():
            if self._closed:
                break
            if self.on_input_transcript is not None:
                try:
                    await self.on_input_transcript(ev)
                except Exception as e:  # noqa: BLE001
                    log.warning("input_transcript_callback_failed", error=str(e))

    # ------------------------------------------------------------------ reconnect
    async def reconnect_translator(self) -> None:
        """Replace the AI session after a failure. Preserves gen id bookkeeping."""
        log.info("translator_reconnect_start",
                 call_id=self.cfg.call_id,
                 participant_id=self.cfg.participant_id)
        try:
            await self.translator.close()
        except Exception:  # noqa: BLE001
            pass
        # New translator instance — keep languages/voice
        self.translator = RealtimeTranslator(
            source_language=self.cfg.source_language,
            target_language=self.cfg.target_language,
        )
        self.interrupt_ctl.translator = self.translator
        await self.translator.connect()
        new_gen = await self.gen_id.bump()
        self.translator.set_generation_id(new_gen)
        self._current_speech_gen = new_gen
        self.metrics.increment_reconnect()
        log.info("translator_reconnect_done", new_gen=new_gen)

    # ------------------------------------------------------------------ stop
    async def stop(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.translator.close()
        except Exception:  # noqa: BLE001
            pass
        self.vad.close()
        await self.playback.stop()
        for t in self._tasks:
            if not t.done():
                t.cancel()
        log.info("session_stopped",
                 call_id=self.cfg.call_id,
                 participant_id=self.cfg.participant_id,
                 metrics=self.metrics.snapshot())

    # ------------------------------------------------------------------ snapshot
    def metrics_snapshot(self) -> dict:
        return self.metrics.snapshot()
