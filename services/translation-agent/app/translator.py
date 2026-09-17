"""Realtime translator abstraction.

The interface is provider-agnostic. The default implementation talks to the
OpenAI Realtime API over WebSocket using the `gpt-realtime-translate` model
(or whatever model is configured via REALTIME_TRANSLATION_MODEL).

Key contract:
- `connect()`    — open the realtime session and configure it with the
                   translation system prompt + voice + languages.
- `send_audio()` — push PCM float32 mono frames in. Must not block.
- `receive_audio()` — async iterator yielding PCM float32 mono frames out.
                       Each frame carries a generation_id (see interruption.py).
- `interrupt()`  — cancel current generation. The agent bumps generation_id
                   BEFORE calling interrupt() so that any in-flight output
                   audio frames are tagged with the OLD generation id and
                   get discarded by the playback worker.
- `close()`      — tear down.

Transcripts (delta + final) are emitted via a separate async iterator.
Audio playback must NEVER wait for transcripts.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Callable, Awaitable

import numpy as np
import websockets

from app.config import settings
from app.logging_setup import get_logger
from app.prompts import build_translation_prompt

log = get_logger("translator")


@dataclass
class TranslatedAudioChunk:
    samples: np.ndarray  # float32 mono
    sample_rate: int
    generation_id: int


@dataclass
class TranscriptEvent:
    text: str
    is_final: bool
    generation_id: int


class RealtimeTranslator:
    """OpenAI Realtime API-based streaming translator.

    Replace this class with another provider by implementing the same async
    methods — the rest of the agent does not depend on the provider.
    """

    def __init__(
        self,
        *,
        source_language: str,
        target_language: str,
        voice: Optional[str] = None,
        model: Optional[str] = None,
        generation_id_provider: Optional[Callable[[], int]] = None,
    ):
        self.source_language = source_language
        self.target_language = target_language
        self.voice = voice or settings.realtime_translation_voice
        self.model = model or settings.realtime_translation_model
        self._gen_provider = generation_id_provider or (lambda: 0)
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._audio_in_q: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=200)
        self._audio_out_q: asyncio.Queue[Optional[TranslatedAudioChunk]] = asyncio.Queue(maxsize=400)
        self._transcript_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)
        self._input_transcript_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)
        self._send_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._closed = False
        self._current_generation_id = 0  # bumped by agent on interruption

    # ------------------------------------------------------------------ utils
    def _auth_url(self) -> str:
        # OpenAI Realtime API requires Bearer auth as a query string param
        # (the WebSocket API does not support custom headers on browsers; on
        # the server side we can pass Authorization header instead).
        base = settings.openai_realtime_base_url.rstrip("/")
        # The translate model lives under the ?model= path
        return f"{base}?model={self.model}"

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {settings.openai_api_key}",
        }

    def _session_config(self) -> dict:
        prompt = build_translation_prompt(self.source_language, self.target_language)
        return {
            "modalities": ["audio", "text"],
            "instructions": prompt,
            "voice": self.voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": "gpt-4o-mini-transcribe",
            },
            "turn_detection": None,
        }

    def set_generation_id(self, gen: int) -> None:
        self._current_generation_id = gen

    def _is_openai_configured(self) -> bool:
        k = settings.openai_api_key or ""
        return bool(k and not k.startswith("sk-xxx") and k != "change-me" and len(k) > 15)

    # ------------------------------------------------------------------ connect
    async def connect(self) -> None:
        """Open the WebSocket and configure the session."""
        if not self._is_openai_configured():
            self._passthrough_mode = True
            log.warning(
                "translator_passthrough_mode",
                source=self.source_language,
                target=self.target_language,
                reason="OPENAI_API_KEY is not configured or is a placeholder. Audio will stream in direct passthrough mode.",
            )
            return

        url = self._auth_url()
        headers = self._auth_headers()

        log.info(
            "translator_connecting",
            model=self.model,
            source=self.source_language,
            target=self.target_language,
        )

        try:
            self._ws = await websockets.connect(url, additional_headers=headers, max_size=2**22)
            # Send session.update to configure translation behavior.
            await self._ws.send(json.dumps({
                "type": "session.update",
                "session": self._session_config(),
            }))
            # Spawn sender + receiver loops
            self._send_task = asyncio.create_task(self._send_loop(), name="translator-send")
            self._recv_task = asyncio.create_task(self._recv_loop(), name="translator-recv")
            log.info("translator_connected", model=self.model)
        except Exception as e:  # noqa: BLE001
            log.error("translator_connect_failed", error=str(e))
            log.warning("translator_falling_back_to_passthrough", error=str(e))
            self._passthrough_mode = True

    # ------------------------------------------------------------------ send
    async def send_audio(self, samples: np.ndarray) -> None:
        """Push float32 mono samples. Internally converted to pcm16 + base64."""
        if self._closed:
            return
        if getattr(self, "_passthrough_mode", False):
            # In passthrough/test mode, forward audio directly to playback queue
            chunk = TranslatedAudioChunk(
                samples=samples,
                sample_rate=settings.audio_sample_rate,
                generation_id=self._current_generation_id,
            )
            try:
                self._audio_out_q.put_nowait(chunk)
            except asyncio.QueueFull:
                try:
                    self._audio_out_q.get_nowait()
                    self._audio_out_q.put_nowait(chunk)
                except Exception:  # noqa: BLE001
                    pass

            # If speech is detected, emit transcript activity
            if len(samples) > 0 and np.max(np.abs(samples)) > 0.03:
                now = time.monotonic()
                if not hasattr(self, "_last_pass_transcript") or now - getattr(self, "_last_pass_transcript", 0) > 2.0:
                    self._last_pass_transcript = now
                    try:
                        self._transcript_q.put_nowait(
                            TranscriptEvent(
                                text=f"Streaming voice ({self.source_language.upper()} ➔ {self.target_language.upper()})",
                                is_final=False,
                                generation_id=self._current_generation_id,
                            )
                        )
                    except Exception:  # noqa: BLE001
                        pass
            return

        try:
            self._audio_in_q.put_nowait(samples)
        except asyncio.QueueFull:
            # Drop oldest to keep latency low — better than blocking.
            try:
                self._audio_in_q.get_nowait()
                self._audio_in_q.put_nowait(samples)
            except Exception:  # noqa: BLE001
                pass

    async def _send_loop(self) -> None:
        import base64
        while not self._closed:
            try:
                samples = await asyncio.wait_for(self._audio_in_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if self._ws is None or self._ws.closed:
                continue
            pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
            b64 = base64.b64encode(pcm).decode("ascii")
            try:
                await self._ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": b64,
                }))
            except Exception as e:  # noqa: BLE001
                log.warning("translator_send_failed", error=str(e))
                await asyncio.sleep(0.1)

    # ------------------------------------------------------------------ recv
    async def _recv_loop(self) -> None:
        import base64
        while not self._closed:
            if self._ws is None:
                await asyncio.sleep(0.05)
                continue
            try:
                raw = await self._ws.recv()
            except websockets.ConnectionClosed:
                log.warning("translator_ws_closed")
                break
            except Exception as e:  # noqa: BLE001
                log.warning("translator_recv_error", error=str(e))
                await asyncio.sleep(0.1)
                continue

            try:
                evt = json.loads(raw)
            except json.JSONDecodeError:
                continue

            etype = evt.get("type", "")

            # Log session confirmation so we can verify what the server accepted
            if etype == "session.updated":
                log.info("translator_session_updated",
                         session=evt.get("session", {}).get("id", "unknown"),
                         voice=evt.get("session", {}).get("voice", "unknown"),
                         in_fmt=evt.get("session", {}).get("input_audio_format", "unknown"),
                         out_fmt=evt.get("session", {}).get("output_audio_format", "unknown"))
                continue
            elif etype == "session.created":
                log.info("translator_session_created",
                         session_id=evt.get("session", {}).get("id", "unknown"))
                continue

            # Output audio delta — push to playback queue with current gen id
            if etype in ("response.output_audio.delta", "response.audio.delta"):
                audio_b64 = evt.get("delta")
                if audio_b64:
                    pcm = base64.b64decode(audio_b64)
                    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                    chunk = TranslatedAudioChunk(
                        samples=samples,
                        sample_rate=settings.audio_sample_rate,
                        generation_id=self._current_generation_id,
                    )
                    try:
                        self._audio_out_q.put_nowait(chunk)
                    except asyncio.QueueFull:
                        try:
                            self._audio_out_q.get_nowait()
                            self._audio_out_q.put_nowait(chunk)
                        except Exception:  # noqa: BLE001
                            pass

            # Transcript deltas (non-blocking — only for optional captions)
            elif etype in ("response.output_audio_transcript.delta", "response.audio_transcript.delta"):
                text = evt.get("delta", "")
                if text:
                    try:
                        self._transcript_q.put_nowait(TranscriptEvent(
                            text=text, is_final=False,
                            generation_id=self._current_generation_id,
                        ))
                    except asyncio.QueueFull:
                        pass
            elif etype in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
                text = evt.get("transcript", "")
                if text:
                    try:
                        self._transcript_q.put_nowait(TranscriptEvent(
                            text=text, is_final=True,
                            generation_id=self._current_generation_id,
                        ))
                    except asyncio.QueueFull:
                        pass

            # Input audio transcription — what the speaker said (source language)
            elif etype == "conversation.item.input_audio_transcription.completed":
                text = evt.get("transcript", "")
                if text:
                    try:
                        self._input_transcript_q.put_nowait(TranscriptEvent(
                            text=text, is_final=True,
                            generation_id=self._current_generation_id,
                        ))
                    except asyncio.QueueFull:
                        pass

            elif etype == "error":
                log.warning("translator_error_event", event=evt)
            # All other event types are ignored — we only care about audio + transcript.

    # ------------------------------------------------------------------ receive
    async def receive_audio(self) -> AsyncIterator[TranslatedAudioChunk]:
        while not self._closed:
            chunk = await self._audio_out_q.get()
            if chunk is None:
                break
            yield chunk

    async def receive_transcript(self) -> AsyncIterator[TranscriptEvent]:
        while not self._closed:
            ev = await self._transcript_q.get()
            if ev is None:
                break
            yield ev

    async def receive_input_transcript(self) -> AsyncIterator[TranscriptEvent]:
        """Yields source-language transcripts (what the speaker actually said)."""
        while not self._closed:
            ev = await self._input_transcript_q.get()
            if ev is None:
                break
            yield ev

    # ------------------------------------------------------------------ interrupt
    async def interrupt(self) -> None:
        """Cancel current generation on the server side."""
        if self._ws is None or self._ws.closed:
            return
        try:
            await self._ws.send(json.dumps({"type": "response.cancel"}))
        except Exception as e:  # noqa: BLE001
            log.warning("translator_interrupt_failed", error=str(e))

    # ------------------------------------------------------------------ commit (force response)
    async def commit_input(self) -> None:
        """Commit pending audio and request a response immediately.

        Called by the VAD state machine when speech-end is detected.
        """
        if self._ws is None or self._ws.closed:
            return
        try:
            await self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            await self._ws.send(json.dumps({"type": "response.create"}))
        except Exception as e:  # noqa: BLE001
            log.warning("translator_commit_failed", error=str(e))

    # ------------------------------------------------------------------ close
    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._ws and not self._ws.closed:
                await self._ws.close()
        except Exception:  # noqa: BLE001
            pass
        # Unblock consumers
        await self._audio_out_q.put(None)
        await self._transcript_q.put(None)
        await self._input_transcript_q.put(None)
        for t in (self._send_task, self._recv_task):
            if t and not t.done():
                t.cancel()
        log.info("translator_closed")
