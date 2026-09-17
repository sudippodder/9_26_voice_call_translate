"""Gemini 2.0 Flash Live realtime translator.

Implements the same interface as `RealtimeTranslator` (translator.py) so the
rest of the agent (session.py, interruption.py, playback.py) doesn't need to
know which provider is in use.

Differences from OpenAI Realtime API:
  - Endpoint: wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent
  - Auth: API key as query parameter
  - Audio: 16 kHz PCM16 (NOT 24 kHz like OpenAI) — we resample
  - Setup: send a `setup` message with model + systemInstruction + voice
  - Input audio: `clientContent.audioChunks[].data` (base64 PCM16, 16kHz mono)
  - Output audio: `serverContent.modelTurn.parts[].inlineData.data` (base64)
  - Turn completion: `clientContent.turnComplete` (server auto-responds)
  - Interruption: send `clientContent.turnComplete` to cancel current generation

Docs: https://ai.google.dev/gemini-api/docs/live
"""
from __future__ import annotations

import asyncio
import base64
import json
from typing import AsyncIterator, Optional

import numpy as np
import websockets

from app.config import settings
from app.logging_setup import get_logger
from app.prompts import build_translation_prompt
from app.translator import TranslatedAudioChunk, TranscriptEvent

log = get_logger("gemini_translator")


# Gemini Live uses 16 kHz PCM16 — half of OpenAI's 24 kHz
GEMINI_SAMPLE_RATE = 16000


class GeminiRealtimeTranslator:
    """Gemini Live API translator — same interface as RealtimeTranslator."""

    def __init__(
        self,
        *,
        source_language: str,
        target_language: str,
        voice: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.source_language = source_language
        self.target_language = target_language
        self.voice = voice or settings.gemini_voice
        self.model = model or settings.gemini_model

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._audio_in_q: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=200)
        self._audio_out_q: asyncio.Queue[Optional[TranslatedAudioChunk]] = asyncio.Queue(maxsize=400)
        self._transcript_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)
        self._input_transcript_q: asyncio.Queue[Optional[TranscriptEvent]] = asyncio.Queue(maxsize=200)
        self._send_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._closed = False
        self._current_generation_id = 0

    # ------------------------------------------------------------------ helpers
    def _ws_url(self) -> str:
        return (
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
            f"?key={settings.gemini_api_key}"
        )

    def _setup_message(self) -> dict:
        prompt = build_translation_prompt(self.source_language, self.target_language)
        return {
            "setup": {
                "model": f"models/{self.model}",
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "thinkingConfig": {"thinkingBudget": 0},
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": self.voice}
                        }
                    },
                },
                "systemInstruction": {
                    "parts": [{"text": prompt}]
                },
                "inputAudioTranscription": {},
            }
        }

    def set_generation_id(self, gen: int) -> None:
        self._current_generation_id = gen

    # ------------------------------------------------------------------ connect
    async def connect(self) -> None:
        """Open the WebSocket and configure the session.

        Tries multiple model names — Gemini Live model names change frequently.
        """
        key = (settings.gemini_api_key or "").strip()
        if not key or key.startswith("AIxxx") or len(key) < 20:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured (or is still the placeholder). "
                "Get a free key at https://aistudio.google.com/apikey and set it in .env"
            )

        url = self._ws_url()

        # Try multiple model names — Gemini Live model names change frequently.
        # As of 2026, the most widely available bidi-capable models are:
        #   - gemini-2.5-flash-native-audio-latest       (most accounts have this)
        #   - gemini-2.5-flash-preview-native-audio-dialog
        #   - gemini-2.5-pro-preview-native-audio-dialog  (Pro variant)
        #   - gemini-2.0-flash-live-001                   (older, may be retired)
        #   - gemini-2.0-flash-exp                        (experimental)
        # Run /api/v1/test/list-gemini-models to see what your key actually supports.
        candidate_models = [self.model]
        fallbacks = [
            "gemini-2.5-flash-native-audio-latest",
            "gemini-2.5-flash-preview-native-audio-dialog",
            "gemini-2.5-pro-preview-native-audio-dialog",
            "gemini-2.0-flash-live-001",
            "gemini-2.0-flash-exp",
        ]
        for m in fallbacks:
            if m not in candidate_models:
                candidate_models.append(m)

        log.info(
            "gemini_connecting",
            trying_models=candidate_models,
            voice=self.voice,
            source=self.source_language,
            target=self.target_language,
        )

        last_error: Optional[str] = None
        for model_name in candidate_models:
            try:
                success = await self._try_connect(url, model_name)
                if success:
                    self.model = model_name  # remember which worked
                    log.info("gemini_connected", model=model_name)
                    return
            except Exception as e:  # noqa: BLE001
                err_str = str(e)
                log.warning("gemini_model_unavailable",
                            model=model_name, error=err_str)
                last_error = err_str
                continue

        # All candidates failed
        raise RuntimeError(
            f"All Gemini Live models tried were rejected. "
            f"Last error: {last_error}. "
            f"\n\nModels tried: {candidate_models}"
            f"\n\nGet the current list of available models from "
            f"https://ai.google.dev/gemini-api/docs/models"
        )

    async def _try_connect(self, url: str, model_name: str) -> bool:
        """Try connecting with a specific model. Returns True on success."""
        try:
            self._ws = await websockets.connect(url, max_size=2**22)
        except Exception as e:  # noqa: BLE001
            err_str = str(e)
            if "invalid_api_key" in err_str or "API key not valid" in err_str:
                raise RuntimeError(
                    "Gemini rejected the API key. Get a new one at "
                    "https://aistudio.google.com/apikey"
                ) from e
            raise

        # Send setup message with this model name
        await self._ws.send(json.dumps(self._setup_message_for(model_name)))

        # Wait for setupComplete or error
        try:
            async for raw_msg in self._ws:
                try:
                    evt = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue
                if "setupComplete" in evt:
                    log.info("gemini_setup_complete", model=model_name, voice=self.voice)
                    # Spawn sender + receiver loops
                    self._send_task = asyncio.create_task(self._send_loop(), name="gemini-send")
                    self._recv_task = asyncio.create_task(self._recv_loop(), name="gemini-recv")
                    return True
                if "error" in evt:
                    err = evt.get("error", {})
                    err_msg = err.get("message", str(evt))
                    log.warning("gemini_setup_error",
                                model=model_name, error=err_msg)
                    try:
                        await self._ws.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._ws = None
                    return False
            # Socket closed without setupComplete
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None
            return False
        except websockets.ConnectionClosed as e:
            log.warning("gemini_socket_closed_during_setup",
                        model=model_name, error=str(e))
            self._ws = None
            return False

    def _setup_message_for(self, model_name: str) -> dict:
        """Build setup message for a specific model name.

        Includes languageCode in speechConfig — required for the 2.5 native
        audio model to know which language to use for output.
        """
        prompt = build_translation_prompt(self.source_language, self.target_language)

        # Map ISO language code → Gemini BCP-47 language code
        GEMINI_LANG_MAP = {
            "en": "en-US",
            "hi": "hi-IN",
            "bn": "bn-IN",
        }
        target_lang_code = GEMINI_LANG_MAP.get(self.target_language, "en-US")

        return {
            "setup": {
                "model": f"models/{model_name}",
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "thinkingConfig": {"thinkingBudget": 0},
                    "speechConfig": {
                        "languageCode": target_lang_code,
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": self.voice}
                        }
                    },
                },
                "systemInstruction": {
                    "parts": [{"text": prompt}]
                },
                "inputAudioTranscription": {},
            }
        }

    # ------------------------------------------------------------------ send
    async def send_audio(self, samples: np.ndarray) -> None:
        """Push float32 mono samples (24 kHz). Resamples to 16 kHz for Gemini."""
        if self._closed:
            return
        try:
            self._audio_in_q.put_nowait(samples)
        except asyncio.QueueFull:
            try:
                self._audio_in_q.get_nowait()
                self._audio_in_q.put_nowait(samples)
            except Exception:  # noqa: BLE001
                pass

    async def _send_loop(self) -> None:
        from app.audio import resample_linear
        while not self._closed:
            try:
                samples = await asyncio.wait_for(self._audio_in_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if self._ws is None or self._ws.closed:
                continue

            # Gemini Live requires 16 kHz PCM16. Resample from 24 kHz if needed.
            if settings.audio_sample_rate != GEMINI_SAMPLE_RATE:
                samples = resample_linear(samples, settings.audio_sample_rate, GEMINI_SAMPLE_RATE)

            pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
            b64 = base64.b64encode(pcm).decode("ascii")

            # Stream audio chunks using realtimeInput.audio
            try:
                await self._ws.send(json.dumps({
                    "realtimeInput": {
                        "audio": {
                            "data": b64,
                            "mimeType": "audio/pcm;rate=16000",
                        }
                    }
                }))
            except Exception as e:  # noqa: BLE001
                log.warning("gemini_send_failed", error=str(e))
                await asyncio.sleep(0.1)

    # ------------------------------------------------------------------ recv
    async def _recv_loop(self) -> None:
        from app.audio import resample_linear
        while not self._closed:
            if self._ws is None:
                await asyncio.sleep(0.05)
                continue
            try:
                raw = await self._ws.recv()
            except websockets.ConnectionClosed:
                log.warning("gemini_ws_closed")
                break
            except Exception as e:  # noqa: BLE001
                log.warning("gemini_recv_error", error=str(e))
                await asyncio.sleep(0.1)
                continue

            try:
                evt = json.loads(raw)
            except json.JSONDecodeError:
                continue

            server_content = evt.get("serverContent")
            if not server_content:
                # Could be toolCall, setupComplete (already handled), etc.
                continue

            # Handle server-side user interruption (barge-in)
            if server_content.get("interrupted"):
                log.info("gemini_interrupted_by_user")
                self._current_generation_id += 1
                while not self._audio_out_q.empty():
                    try:
                        self._audio_out_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break

            # Handle caller input speech transcription from Gemini
            input_tx = server_content.get("inputTranscription")
            if input_tx and input_tx.get("text"):
                try:
                    self._input_transcript_q.put_nowait(TranscriptEvent(
                        text=input_tx["text"],
                        is_final=True,
                        generation_id=self._current_generation_id,
                    ))
                except asyncio.QueueFull:
                    pass

            model_turn = server_content.get("modelTurn", {})
            parts = model_turn.get("parts", [])

            for part in parts:
                # Output audio
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    b64 = inline["data"]
                    pcm = base64.b64decode(b64)
                    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                    # Gemini Live outputs 16 kHz — upsample to 24 kHz for our pipeline
                    if GEMINI_SAMPLE_RATE != settings.audio_sample_rate:
                        samples = resample_linear(samples, GEMINI_SAMPLE_RATE, settings.audio_sample_rate)
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

                # Output transcript (text version of what was spoken, excluding thinking)
                if part.get("text") and not part.get("thought"):
                    try:
                        self._transcript_q.put_nowait(TranscriptEvent(
                            text=part["text"], is_final=False,
                            generation_id=self._current_generation_id,
                        ))
                    except asyncio.QueueFull:
                        pass

            # Check for turn completion
            if server_content.get("generationComplete"):
                pass

            # Error handling
            if "error" in evt:
                err = evt["error"]
                log.error("gemini_error_event", error=err)

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
        while not self._closed:
            ev = await self._input_transcript_q.get()
            if ev is None:
                break
            yield ev

    # ------------------------------------------------------------------ interrupt
    async def interrupt(self) -> None:
        """Cancel current generation on the client side."""
        self._current_generation_id += 1
        while not self._audio_out_q.empty():
            try:
                self._audio_out_q.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ------------------------------------------------------------------ commit
    async def commit_input(self) -> None:
        """Mark turn complete — send audioStreamEnd so Gemini responds."""
        if self._ws is None or self._ws.closed:
            return
        try:
            await self._ws.send(json.dumps({
                "realtimeInput": {"audioStreamEnd": True}
            }))
        except Exception as e:  # noqa: BLE001
            log.warning("gemini_commit_failed", error=str(e))

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
        await self._audio_out_q.put(None)
        await self._transcript_q.put(None)
        await self._input_transcript_q.put(None)
        for t in (self._send_task, self._recv_task):
            if t and not t.done():
                t.cancel()
        log.info("gemini_closed")
