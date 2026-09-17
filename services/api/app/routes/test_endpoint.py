"""Test endpoint — accepts audio, runs through OpenAI Realtime, returns translation.

Used by the frontend "Mic Test & Translation Test" screen so users can verify
their mic works and hear the translation quality before making a real call.

NOT on the realtime audio path during actual calls — this is a dev/test tool
that does a one-shot translation.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from typing import Optional

import numpy as np
import websockets
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.auth import get_current_user
from app.config import settings
from app.logging_setup import get_logger
from app.models.schemas import UserOut
from app.prompts import build_translation_prompt

log = get_logger("test_translate")
router = APIRouter()

import wave


def _webm_to_pcm16(audio_bytes: bytes) -> bytes:
    """Convert webm/opus (from MediaRecorder) to PCM16 24kHz mono via ffmpeg."""
    import subprocess
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as inf:
        inf.write(audio_bytes)
        in_path = inf.name
    out_path = in_path.replace(".webm", ".wav")

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", in_path, "-ar", "24000", "-ac", "1",
             "-sample_fmt", "s16", out_path],
            capture_output=True, timeout=15,
        )
        if result.returncode != 0:
            err = (result.stderr or b"no stderr").decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"ffmpeg failed (exit {result.returncode}): {err}")
        with open(out_path, "rb") as f:
            wav_bytes = f.read()
        return _wav_to_pcm16(wav_bytes)
    finally:
        for p in (in_path, out_path):
            try: os.unlink(p)
            except: pass


def _wav_to_pcm16(wav_bytes: bytes) -> bytes:
    """Extract PCM16 samples from a WAV file."""
    with io.BytesIO(wav_bytes) as buf:
        with wave.open(buf, "rb") as wf:
            return wf.readframes(wf.getnframes())


def _pcm16_to_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
    """Wrap raw PCM16 bytes in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _detect_format(raw: bytes) -> str:
    """Detect audio format from magic bytes."""
    if len(raw) < 4:
        return "unknown"
    if raw[:4] == b"RIFF":
        return "wav"
    if raw[:4] == b"OggS":
        return "ogg"
    # WebM / Matroska EBML header
    if raw[0] == 0x1A and raw[1] == 0x45 and raw[2] == 0xDF and raw[3] == 0xA3:
        return "webm"
    # MP3 (ID3 tag or frame sync)
    if raw[:3] == b"ID3" or (raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0):
        return "mp3"
    return "unknown"


@router.post("/api/v1/test/translate")
async def test_translate(
    audio: UploadFile = File(...),
    source_language: str = Form("en"),
    target_language: str = Form("hi"),
    user: UserOut = Depends(get_current_user),
) -> Response:
    """One-shot translation test — record audio, send to AI provider, return translated audio."""
    try:
        # --- Validate provider config ---
        provider = (settings.translator_provider or "openai").lower().strip()
        key = (settings.openai_api_key or "").strip()
        gemini_key = (settings.gemini_api_key or "").strip()

        if provider == "gemini":
            if not gemini_key or gemini_key.startswith("AIxxx") or len(gemini_key) < 20:
                raise HTTPException(
                    status_code=503,
                    detail="GEMINI_API_KEY is not configured. "
                           "Get a free key at https://aistudio.google.com/apikey "
                           "and set GEMINI_API_KEY in .env",
                )
        else:  # openai
            if not key or key.startswith("sk-xxx") or len(key) < 20:
                raise HTTPException(
                    status_code=503,
                    detail="OPENAI_API_KEY is not configured on the server. "
                           "Set a valid key in .env and restart the API container. "
                           "Or switch to Gemini: set TRANSLATOR_PROVIDER=gemini + GEMINI_API_KEY.",
                )

        # --- Read + validate audio ---
        raw = await audio.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Empty audio file")

        fmt = _detect_format(raw)
        log.info("test_translate_audio_received",
                 format=fmt, bytes=len(raw), source=source_language, target=target_language)

        if fmt == "wav":
            pcm16 = _wav_to_pcm16(raw)
        elif fmt in ("webm", "ogg", "mp3"):
            try:
                pcm16 = _webm_to_pcm16(raw)
            except FileNotFoundError:
                raise HTTPException(
                    status_code=500,
                    detail="ffmpeg is not installed in the API container. "
                           "Rebuild: docker compose build --no-cache api",
                )
            except RuntimeError as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Audio conversion failed: {str(e)[:300]}",
                )
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Audio conversion error: {type(e).__name__}: {str(e)[:300]}",
                )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported audio format (magic bytes: {raw[:8].hex()}). "
                       "Use WAV or WebM/Opus (from MediaRecorder).",
            )

        if len(pcm16) < 4800:  # < 100ms at 24kHz
            raise HTTPException(
                status_code=400,
                detail=f"Audio too short ({len(pcm16)} bytes = "
                       f"{len(pcm16) / 48000:.2f}s). Need at least 100ms of speech.",
            )

        # --- Pick provider based on TRANSLATOR_PROVIDER config ---
        provider = (settings.translator_provider or "openai").lower().strip()
        log.info("test_translate_provider", provider=provider,
                 source=source_language, target=target_language)

        if provider == "gemini":
            return await _run_gemini_translation(
                pcm16, source_language, target_language, user,
            )
        # Default: OpenAI
        return await _run_openai_translation(pcm16, source_language, target_language, user, key)

    except HTTPException:
        raise
    except Exception as e:
        # Catch-all: return a clear 500 with the actual error message
        log.error("test_translate_unhandled_error",
                  error_type=type(e).__name__, error_msg=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Internal error: {type(e).__name__}: {str(e)[:300]}",
        )


async def _run_openai_translation(
    pcm16: bytes, source_language: str, target_language: str, user, key: str,
) -> Response:
    """Original OpenAI Realtime translation path."""
    prompt = build_translation_prompt(source_language, target_language)

    log.info("test_translate_connecting_openai", model=settings.realtime_translation_model)

    try:
        ws = await asyncio.wait_for(
            _connect_with_fallback(key, settings.realtime_translation_model),
            timeout=15.0,
        )
    except _ModelNotFoundError as e:
        raise HTTPException(
            status_code=502,
            detail=(
                f"OpenAI rejected ALL realtime model names tried. "
                f"This means your OpenAI account doesn't have Realtime API access. "
                f"\n\nTo fix:\n"
                f"  1. Visit https://platform.openai.com/account/billing — add a credit card\n"
                f"  2. Visit https://platform.openai.com/account/limits — check your usage tier\n"
                f"  3. Realtime API requires Tier 2 or higher\n"
                f"  4. Run this diagnostic: curl http://localhost:8000/api/v1/test/list-models "
                f"(after logging in with a Bearer token) to see what models your key can access\n"
                f"\nAlternatively, switch to Gemini by setting TRANSLATOR_PROVIDER=gemini "
                f"and GEMINI_API_KEY=<your-key> in .env — Gemini has a free tier.\n"
                f"\nDetail: {e}"
            ),
        )
    except _InvalidKeyError:
        raise HTTPException(
            status_code=502,
            detail="OpenAI rejected the API key. Check OPENAI_API_KEY in .env.",
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="OpenAI Realtime API connection timed out (15s). "
                   "Check your internet connection.",
        )
    except Exception as e:
        err_str = str(e)
        log.error("test_translate_connect_failed", error=err_str)
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI connection failed: {err_str[:300]}",
        )

    # --- Send audio + collect response ---
    try:
        # Configure session
        await ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "instructions": prompt,
                "voice": settings.realtime_translation_voice,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": None,
                "modalities": ["audio", "text"],
            },
        }))

        # Send audio in 100ms chunks (2400 samples * 2 bytes = 4800 bytes)
        chunk_size = 4800
        for i in range(0, len(pcm16), chunk_size):
            chunk = pcm16[i:i + chunk_size]
            await ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode("ascii"),
            }))
            # Yield to event loop periodically
            if (i // chunk_size) % 5 == 0:
                await asyncio.sleep(0)

        # Commit + request response
        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        await ws.send(json.dumps({"type": "response.create"}))

        # Collect response
        translated_pcm = bytearray()
        transcript_text = ""
        start_time = time.monotonic()

        async for raw_msg in ws:
            if time.monotonic() - start_time > 30:
                log.warning("test_translate_timeout_after_30s")
                break
            try:
                evt = json.loads(raw_msg)
            except json.JSONDecodeError:
                continue

            etype = evt.get("type", "")
            if etype in ("response.output_audio.delta", "response.audio.delta"):
                audio_b64 = evt.get("delta")
                if audio_b64:
                    translated_pcm.extend(base64.b64decode(audio_b64))
            elif etype in ("response.output_audio_transcript.delta", "response.audio_transcript.delta"):
                transcript_text += evt.get("delta", "")
            elif etype in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
                transcript_text = evt.get("transcript", transcript_text)
            elif etype == "response.done":
                break
            elif etype == "error":
                err = evt.get("error", {})
                err_msg = err.get("message", str(evt))
                log.error("test_translate_openai_error", event=evt)
                raise HTTPException(
                    status_code=502,
                    detail=f"OpenAI error: {err_msg[:300]}",
                )
    finally:
        try:
            await ws.close()
        except Exception:
            pass

    if not translated_pcm:
        raise HTTPException(
            status_code=502,
            detail="OpenAI returned no audio. Check your API key has Realtime API access.",
        )

    log.info("test_translate_done_openai",
             translated_bytes=len(translated_pcm),
             transcript=transcript_text[:200],
             elapsed_ms=int((time.monotonic() - start_time) * 1000))

    # Return WAV audio
    wav_bytes = _pcm16_to_wav(bytes(translated_pcm), sample_rate=24000)
    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Transcript": transcript_text[:500].encode("utf-8", errors="replace").decode("latin-1"),
            "X-Translation-Seconds": f"{len(translated_pcm) / (2 * 24000):.2f}",
            "X-Provider": "openai",
            "Access-Control-Expose-Headers": "X-Transcript, X-Translation-Seconds, X-Provider",
        },
    )


async def _run_gemini_translation(
    pcm16: bytes, source_language: str, target_language: str, user,
) -> Response:
    """Gemini Live API translation path. Tries multiple model names."""
    import websockets

    key = (settings.gemini_api_key or "").strip()
    if not key or key.startswith("AIxxx") or len(key) < 20:
        raise HTTPException(
            status_code=503,
            detail="GEMINI_API_KEY is not configured. Get a free key at "
                   "https://aistudio.google.com/apikey and set it in .env",
        )

    # Resample 24kHz PCM16 to 16kHz for Gemini Live
    samples_24k = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    samples_16k = _resample(samples_24k, 24000, 16000)
    pcm16_16k = (np.clip(samples_16k, -1, 1) * 32767).astype(np.int16).tobytes()

    prompt = build_translation_prompt(source_language, target_language)
    base_url = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
        f"?key={key}"
    )

    # Try multiple model names — Gemini renames these frequently
    # As of 2026, the most widely available bidi-capable models are:
    #   - gemini-2.5-flash-native-audio-latest       (most accounts have this)
    #   - gemini-2.5-flash-preview-native-audio-dialog
    #   - gemini-2.5-pro-preview-native-audio-dialog  (Pro variant)
    #   - gemini-2.0-flash-live-001                   (older, may be retired)
    #   - gemini-2.0-flash-exp                        (experimental)
    candidate_models = [settings.gemini_model]
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

    log.info("test_translate_gemini_trying_models", models=candidate_models)

    ws = None
    last_error = None
    used_model = None

    # Map ISO language code → Gemini BCP-47 language code (for speechConfig.languageCode)
    GEMINI_LANG_MAP = {
        "en": "en-US",
        "hi": "hi-IN",
        "bn": "bn-IN",
    }
    target_lang_code = GEMINI_LANG_MAP.get(target_language, "en-US")
    source_lang_code = GEMINI_LANG_MAP.get(source_language, "en-US")

    for model_name in candidate_models:
        log.info("gemini_connecting", model=model_name)
        try:
            ws = await asyncio.wait_for(
                websockets.connect(base_url, max_size=2**22),
                timeout=15.0,
            )
            # Setup config — minimal version that works for both 2.0 and 2.5 models.
            # IMPORTANT: For 2.5 native audio, voiceName is REQUIRED but must be a
            # supported voice (Aoede, Charon, Fenrir, Leda, Orus, Puck).
            setup_msg = {
                "setup": {
                    "model": f"models/{model_name}",
                    "generationConfig": {
                        "responseModalities": ["AUDIO"],
                        "thinkingConfig": {"thinkingBudget": 0},
                        "speechConfig": {
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {"voiceName": settings.gemini_voice}
                            }
                        },
                    },
                    "systemInstruction": {
                        "parts": [{"text": prompt}]
                    },
                }
            }

            # Log the exact JSON being sent — useful for debugging
            log.info("gemini_setup_request", model=model_name, payload=json.dumps(setup_msg)[:500])

            await ws.send(json.dumps(setup_msg))

            # Wait for setupComplete or error
            setup_done = False
            start = time.monotonic()
            async for raw in ws:
                if time.monotonic() - start > 10:
                    break
                try:
                    evt = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if "setupComplete" in evt:
                    setup_done = True
                    break
                if "error" in evt:
                    err_msg = evt["error"].get("message", str(evt))
                    log.warning("gemini_model_rejected",
                                model=model_name, error=err_msg)
                    try: await ws.close()
                    except: pass
                    ws = None
                    last_error = err_msg
                    break  # try next model

            if setup_done:
                used_model = model_name
                log.info("gemini_setup_complete", model=model_name)
                # Give Gemini a moment to fully initialize
                await asyncio.sleep(0.3)
                break

        except Exception as e:
            err_str = str(e)
            log.warning("gemini_connect_failed", model=model_name, error=err_str)
            last_error = err_str
            if ws:
                try: await ws.close()
                except: pass
                ws = None
            continue

    if ws is None or used_model is None:
        raise HTTPException(
            status_code=502,
            detail=(
                f"All Gemini models tried were rejected. Last error: {last_error}. "
                f"\n\nTry this diagnostic to see what models your key can access:\n"
                f"  curl http://localhost:8000/api/v1/test/list-gemini-models -H 'Authorization: Bearer <token>'\n"
                f"\nModels tried: {candidate_models}"
            ),
        )

    try:
        # Send audio in chunks via realtimeInput.audio (100ms at 16kHz PCM16)
        chunk_size = 3200

        log.info("gemini_sending_audio",
                 total_bytes=len(pcm16_16k),
                 chunks=(len(pcm16_16k) + chunk_size - 1) // chunk_size,
                 model=used_model)

        for i in range(0, len(pcm16_16k), chunk_size):
            chunk = pcm16_16k[i:i + chunk_size]
            await ws.send(json.dumps({
                "realtimeInput": {
                    "audio": {
                        "data": base64.b64encode(chunk).decode("ascii"),
                        "mimeType": "audio/pcm;rate=16000",
                    }
                }
            }))
            # Yield to event loop periodically
            if (i // chunk_size) % 5 == 0:
                await asyncio.sleep(0.01)

        # Mark turn complete via audioStreamEnd
        await ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
        log.info("gemini_audio_stream_end_sent")

        # Collect response
        translated_pcm_16k = bytearray()
        transcript_text = ""
        start_time = time.monotonic()

        async for raw_msg in ws:
            if time.monotonic() - start_time > 30:
                log.warning("gemini_test_timeout")
                break
            try:
                evt = json.loads(raw_msg)
            except json.JSONDecodeError:
                continue

            server_content = evt.get("serverContent")
            if not server_content:
                # Could be an error message
                if "error" in evt:
                    err = evt["error"]
                    err_msg = err.get("message", str(evt))
                    log.error("gemini_response_error", error=err_msg, event=evt)
                    raise HTTPException(
                        status_code=502,
                        detail=f"Gemini response error: {err_msg[:300]}",
                    )
                continue

            model_turn = server_content.get("modelTurn", {})
            for part in model_turn.get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    translated_pcm_16k.extend(base64.b64decode(inline["data"]))
                if part.get("text") and not part.get("thought"):
                    transcript_text += part["text"]

            if server_content.get("generationComplete"):
                break

    except websockets.ConnectionClosed as e:
        # Capture the exact error from Gemini
        log.error("gemini_ws_closed_during_audio",
                  code=e.code, reason=str(e.reason)[:300])
        raise HTTPException(
            status_code=502,
            detail=f"Gemini closed connection (code {e.code}): {str(e.reason)[:300]}",
        )
    finally:
        try:
            await ws.close()
        except Exception:
            pass

    if not translated_pcm_16k:
        raise HTTPException(
            status_code=502,
            detail="Gemini returned no audio. Check your API key has Live API access.",
        )

    # Upsample 16kHz → 24kHz so the audio plays at correct pitch
    samples_16k = np.frombuffer(bytes(translated_pcm_16k), dtype=np.int16).astype(np.float32) / 32768.0
    samples_24k = _resample(samples_16k, 16000, 24000)
    pcm16_24k = (np.clip(samples_24k, -1, 1) * 32767).astype(np.int16).tobytes()

    log.info("test_translate_done_gemini",
             model=used_model,
             translated_bytes=len(pcm16_24k),
             transcript=transcript_text[:200],
             elapsed_ms=int((time.monotonic() - start_time) * 1000))

    wav_bytes = _pcm16_to_wav(pcm16_24k, sample_rate=24000)
    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Transcript": transcript_text[:500].encode("utf-8", errors="replace").decode("latin-1"),
            "X-Translation-Seconds": f"{len(pcm16_24k) / (2 * 24000):.2f}",
            "X-Provider": f"gemini ({used_model})",
            "Access-Control-Expose-Headers": "X-Transcript, X-Translation-Seconds, X-Provider",
        },
    )


def _resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear resampling."""
    if src_rate == dst_rate or len(samples) == 0:
        return samples
    ratio = dst_rate / src_rate
    n_out = max(1, int(len(samples) * ratio))
    idx = np.linspace(0, len(samples) - 1, n_out)
    return np.interp(idx, np.arange(len(samples)), samples).astype(np.float32)


class _ModelNotFoundError(Exception):
    pass


class _InvalidKeyError(Exception):
    pass


async def _connect_with_fallback(api_key: str, configured_model: str):
    """Try multiple model names until one works.

    Connects to the WebSocket AND sends session.update to verify the model
    is actually usable. If session.update fails with model_not_found, tries
    the next candidate model.
    """
    import websockets

    candidates = [configured_model]
    for m in ("gpt-4o-realtime-preview", "gpt-4o-realtime-preview-2024-12-17"):
        if m not in candidates:
            candidates.append(m)

    last_err: Optional[Exception] = None
    for model_name in candidates:
        url = f"{settings.openai_realtime_base_url.rstrip('/')}?model={model_name}"
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            ws = await websockets.connect(url, additional_headers=headers, max_size=2**22)
        except Exception as e:
            err_str = str(e)
            if "model_not_found" in err_str:
                last_err = _ModelNotFoundError(f"Model '{model_name}' not found (connect)")
                continue
            if "invalid_api_key" in err_str:
                raise _InvalidKeyError("Invalid API key")
            raise

        # Try sending session.update — OpenAI may close the socket after
        # receiving this if the model is invalid.
        try:
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "instructions": "test",
                    "voice": settings.realtime_translation_voice,
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": None,
                },
            }))
            # Wait briefly to see if OpenAI rejects it
            try:
                first_msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
                evt = json.loads(first_msg)
                if evt.get("type") == "error":
                    err = evt.get("error", {})
                    if err.get("code") == "model_not_found" or "model_not_found" in err.get("message", ""):
                        await ws.close()
                        last_err = _ModelNotFoundError(
                            f"Model '{model_name}' rejected by OpenAI after session.update"
                        )
                        continue
                # Otherwise we're good — this model works
                log.info("test_translate_connected", model=model_name)
                return ws
            except asyncio.TimeoutError:
                # No response in 3s — assume session.update was accepted
                # (OpenAI doesn't always send a session.updated event immediately)
                log.info("test_translate_connected_no_response", model=model_name)
                return ws
            except websockets.ConnectionClosed as e:
                err_str = str(e)
                if "model_not_found" in err_str:
                    last_err = _ModelNotFoundError(
                        f"Model '{model_name}' rejected by OpenAI (socket closed)"
                    )
                    continue
                raise
        except _ModelNotFoundError:
            continue
        except _InvalidKeyError:
            raise
        except Exception as e:
            err_str = str(e)
            if "model_not_found" in err_str:
                last_err = _ModelNotFoundError(f"Model '{model_name}' not found")
                continue
            if "invalid_api_key" in err_str:
                raise _InvalidKeyError("Invalid API key")
            raise

    raise last_err or _ModelNotFoundError("All models failed")


@router.get("/api/v1/test/gemini-ping")
async def gemini_ping() -> dict:
    """Tests Gemini Live API connectivity by opening a WebSocket and sending setup.

    Returns the raw setupComplete response or error message.
    Useful for diagnosing whether the issue is setup config or audio sending.
    """
    import websockets

    key = (settings.gemini_api_key or "").strip()
    if not key or key.startswith("AIxxx"):
        return {"ok": False, "error": "GEMINI_API_KEY not configured"}

    url = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
        f"?key={key}"
    )

    # Try the user's configured model + fallbacks
    candidates = [settings.gemini_model, "gemini-2.5-flash-native-audio-latest"]
    for m in ("gemini-2.5-flash-preview-native-audio-dialog",
              "gemini-2.0-flash-live-001", "gemini-2.0-flash-exp"):
        if m not in candidates:
            candidates.append(m)

    results = []
    for model_name in candidates:
        # Try a minimal setup — no voice, no systemInstruction, just AUDIO modality
        for voice_name in ["Aoede", "Puck", "Charon", None]:
            try:
                ws = await asyncio.wait_for(
                    websockets.connect(url, max_size=2**22),
                    timeout=10.0,
                )
                setup = {
                    "setup": {
                        "model": f"models/{model_name}",
                        "generationConfig": {
                            "responseModalities": ["AUDIO"],
                        },
                    }
                }
                if voice_name:
                    setup["setup"]["generationConfig"]["speechConfig"] = {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": voice_name}
                        }
                    }
                await ws.send(json.dumps(setup))
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    evt = json.loads(raw)
                    if "setupComplete" in evt:
                        results.append({
                            "model": model_name,
                            "voice": voice_name,
                            "status": "OK",
                            "detail": "setupComplete received",
                        })
                        try: await ws.close()
                        except: pass
                        return {"ok": True, "working_config": results[-1], "all_results": results}
                    elif "error" in evt:
                        results.append({
                            "model": model_name,
                            "voice": voice_name,
                            "status": "error",
                            "detail": evt["error"].get("message", str(evt))[:200],
                        })
                    else:
                        results.append({
                            "model": model_name,
                            "voice": voice_name,
                            "status": "unknown",
                            "detail": str(evt)[:200],
                        })
                except asyncio.TimeoutError:
                    results.append({
                        "model": model_name, "voice": voice_name,
                        "status": "timeout", "detail": "no response in 5s",
                    })
                try: await ws.close()
                except: pass
            except Exception as e:
                results.append({
                    "model": model_name, "voice": voice_name,
                    "status": "exception", "detail": str(e)[:200],
                })

    return {"ok": False, "all_results": results, "hint": "None of the model/voice combos worked. Check the results array for details."}


@router.get("/api/v1/test/list-gemini-models")
async def list_gemini_models(user: UserOut = Depends(get_current_user)) -> dict:
    """List models available to the Gemini account — used to find the right
    bidirectional Live model name.

    Gemini Live model names change frequently. This endpoint lists all models
    available to your key, filtered to show the ones that might support live audio.
    """
    import httpx

    key = (settings.gemini_api_key or "").strip()
    if not key or key.startswith("AIxxx"):
        return {
            "configured": False,
            "live_models": [],
            "hint": "GEMINI_API_KEY not configured in .env",
        }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            )
        if r.status_code != 200:
            return {
                "configured": True,
                "live_models": [],
                "hint": f"Gemini /v1beta/models returned {r.status_code}: {r.text[:300]}",
            }

        data = r.json()
        all_models = data.get("models", [])
        # Filter for Live / audio-dialog variants
        live_models = []
        all_names = []
        for m in all_models:
            name = m.get("name", "")
            display_name = m.get("displayName", "")
            # Strip the "models/" prefix
            short_name = name.replace("models/", "") if name.startswith("models/") else name
            all_names.append(short_name)
            name_lower = name.lower()
            display_lower = display_name.lower()
            # Look for live / audio dialog / bidi support
            if any(kw in name_lower or kw in display_lower for kw in
                   ["live", "audio-dialog", "bidi", "2.5-flash", "2.0-flash"]):
                # Check supportedGenerationMethods for bidiGenerateContent
                methods = m.get("supportedGenerationMethods", [])
                live_models.append({
                    "name": short_name,
                    "display_name": display_name,
                    "supports_bidi": "bidiGenerateContent" in methods,
                    "methods": methods,
                })

        return {
            "configured": True,
            "live_models": live_models,
            "all_count": len(all_models),
            "hint": (
                f"Look for models where supports_bidi=true. "
                f"If none have it, your account may not have Live API access yet. "
                f"Try the free tier at https://aistudio.google.com/apikey"
                if not any(m["supports_bidi"] for m in live_models)
                else "Found bidi-supporting models — use one of these as GEMINI_MODEL"
            ),
        }
    except Exception as e:
        return {
            "configured": True,
            "live_models": [],
            "hint": f"Failed to fetch: {type(e).__name__}: {e}",
        }


@router.get("/api/v1/test/openai-status")
async def openai_status() -> dict:
    """Check AI provider configuration. Used by the mic test UI."""
    provider = (settings.translator_provider or "openai").lower().strip()
    openai_key = (settings.openai_api_key or "").strip()
    gemini_key = (settings.gemini_api_key or "").strip()

    openai_configured = bool(openai_key) and not openai_key.startswith("sk-xxx") and len(openai_key) > 20
    gemini_configured = bool(gemini_key) and not gemini_key.startswith("AIxxx") and len(gemini_key) > 20

    if provider == "gemini":
        return {
            "provider": "gemini",
            "configured": gemini_configured,
            "model": settings.gemini_model,
            "voice": settings.gemini_voice,
            "hint": (
                "Using Gemini Live API. "
                + ("Configured ✓" if gemini_configured else "GEMINI_API_KEY missing — get one at https://aistudio.google.com/apikey")
            ),
        }

    # Default: openai
    return {
        "provider": "openai",
        "configured": openai_configured,
        "model": settings.realtime_translation_model,
        "voice": settings.realtime_translation_voice,
        "hint": (
            "Using OpenAI Realtime API. "
            + ("Configured ✓" if openai_configured else "OPENAI_API_KEY missing or invalid")
        ),
    }


@router.get("/api/v1/test/list-models")
async def list_models(user: UserOut = Depends(get_current_user)) -> dict:
    """List models available to the OpenAI account — used to diagnose
    Realtime API access issues.

    Realtime API access requires:
    - Tier 2 or higher usage tier (https://platform.openai.com/account/limits)
    - A valid payment method on file (https://platform.openai.com/account/billing)

    If `realtime_models` is empty, your account doesn't have Realtime API access.
    """
    import httpx

    key = (settings.openai_api_key or "").strip()
    if not key or key.startswith("sk-xxx"):
        return {
            "configured": False,
            "realtime_models": [],
            "all_count": 0,
            "hint": "OPENAI_API_KEY not configured in .env",
        }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {key}"},
            )
        if r.status_code == 401:
            return {
                "configured": True,
                "realtime_models": [],
                "all_count": 0,
                "hint": "OpenAI rejected the API key. Check OPENAI_API_KEY.",
            }
        if r.status_code != 200:
            return {
                "configured": True,
                "realtime_models": [],
                "all_count": 0,
                "hint": f"OpenAI /v1/models returned {r.status_code}: {r.text[:200]}",
            }

        data = r.json()
        all_models = [m["id"] for m in data.get("data", [])]
        # Filter for realtime-related models
        realtime_models = sorted([
            m for m in all_models
            if "realtime" in m.lower() or "audio" in m.lower()
        ])

        return {
            "configured": True,
            "realtime_models": realtime_models,
            "all_count": len(all_models),
            "hint": (
                "If realtime_models is empty, your OpenAI account doesn't have "
                "Realtime API access. Requirements: Tier 2+ usage tier + payment method. "
                "Check https://platform.openai.com/account/limits and "
                "https://platform.openai.com/account/billing"
                if not realtime_models
                else "Realtime API access confirmed."
            ),
        }
    except Exception as e:
        return {
            "configured": True,
            "realtime_models": [],
            "all_count": 0,
            "hint": f"Failed to fetch models: {type(e).__name__}: {e}",
        }
