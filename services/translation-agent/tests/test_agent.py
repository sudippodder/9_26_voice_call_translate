"""Tests for the translation agent — VAD, generation IDs, playback, audio."""
import asyncio
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_pcm16_to_float32_roundtrip():
    from app.audio import pcm16_to_float32, float32_to_pcm16
    samples = np.array([0.5, -0.5, 0.0, 0.25], dtype=np.float32)
    pcm = float32_to_pcm16(samples)
    back = pcm16_to_float32(pcm)
    assert len(back) == len(samples)
    # int16 quantization step is 1/32767 ≈ 3.05e-5 — allow 4e-5 tolerance
    assert np.allclose(back, samples, atol=4e-5)


def test_resample_linear_48k_to_24k():
    from app.audio import resample_linear
    # 1 second of 480 Hz sine at 48 kHz
    t = np.linspace(0, 1, 48000, endpoint=False)
    src = np.sin(2 * np.pi * 480 * t).astype(np.float32)
    out = resample_linear(src, 48000, 24000)
    assert abs(len(out) - 24000) < 50


def test_vad_speech_started_and_ended():
    """Energy-based VAD should detect a loud region as speech.

    The VAD uses wall-clock time for prefix-padding / silence-duration
    detection — so we feed audio with real inter-chunk delays (as in
    production, where audio frames arrive in real time).
    """
    from app.vad import StreamingVAD, VadEventKind

    async def run():
        vad = StreamingVAD(
            prefix_padding_ms=50,
            silence_duration_ms=200,
            energy_threshold=0.02,
        )
        events: list = []

        async def drain():
            async for ev in vad.events():
                events.append(ev)

        task = asyncio.create_task(drain())

        # 500ms of silence to settle noise floor
        silence = np.zeros(1200, dtype=np.float32)  # 50ms @ 24kHz
        for _ in range(10):
            vad.push(silence)
            await asyncio.sleep(0.05)

        # 700ms of speech-level signal
        t = np.linspace(0, 0.05, 1200, endpoint=False)
        speech_chunk = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        for _ in range(14):
            vad.push(speech_chunk)
            await asyncio.sleep(0.05)

        # 600ms of silence to trigger SPEECH_ENDED
        for _ in range(12):
            vad.push(silence)
            await asyncio.sleep(0.05)

        await asyncio.sleep(0.1)
        vad.close()
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return events

    events = asyncio.run(run())
    kinds = [e.kind for e in events]
    assert VadEventKind.SPEECH_STARTED in kinds, f"VAD did not fire SPEECH_STARTED; got {kinds}"
    assert VadEventKind.SPEECH_ENDED in kinds, f"VAD did not fire SPEECH_ENDED; got {kinds}"


def test_generation_id_bump_and_is_current():
    """GenerationId should be monotonic and is_current should track the latest."""
    from app.interruption import GenerationId
    gen = GenerationId()
    assert gen.value == 0
    g1 = asyncio.run(gen.bump())
    assert g1 == 1
    g2 = asyncio.run(gen.bump())
    assert g2 == 2
    assert gen.is_current(g2)
    assert not gen.is_current(g1)


def test_playback_queue_drops_stale_chunks():
    """A chunk tagged with an old generation_id must be silently dropped."""
    from app.interruption import GenerationId
    from app.metrics import SessionMetrics
    from app.playback import PlaybackQueue
    from app.translator import TranslatedAudioChunk

    async def run():
        gen = GenerationId()
        # Start at generation 1
        await gen.bump()
        m = SessionMetrics(
            call_id="c1", participant_id="p1",
            source_language="en", target_language="hi",
        )
        q = PlaybackQueue(gen_id=gen, metrics=m)

        # Current generation chunk — should be enqueued
        c1 = TranslatedAudioChunk(
            samples=np.zeros(2400, dtype=np.float32),
            sample_rate=24000,
            generation_id=1,
        )
        await q.put(c1)

        # Bump to generation 2 (interruption)
        await gen.bump()

        # Old-generation chunk — must be discarded
        c2 = TranslatedAudioChunk(
            samples=np.zeros(2400, dtype=np.float32),
            sample_rate=24000,
            generation_id=1,  # stale
        )
        await q.put(c2)

        # New-generation chunk — should be enqueued
        c3 = TranslatedAudioChunk(
            samples=np.zeros(2400, dtype=np.float32),
            sample_rate=24000,
            generation_id=2,
        )
        await q.put(c3)

        # Drain queue manually — should contain c1 and c3 but NOT c2
        items = []
        while not q._q.empty():
            items.append(q._q.get_nowait())
        gens = [it.generation_id for it in items if it is not None]
        assert 1 in gens  # c1
        assert 2 in gens  # c3
        # c2 (generation 1 after bump) should NOT have been enqueued
        # (c1 is generation 1 from BEFORE bump — still allowed at enqueue time)
        assert len([g for g in gens if g == 1]) == 1

    asyncio.run(run())


def test_playback_clear_drops_all():
    from app.interruption import GenerationId
    from app.metrics import SessionMetrics
    from app.playback import PlaybackQueue
    from app.translator import TranslatedAudioChunk

    async def run():
        gen = GenerationId()
        await gen.bump()
        m = SessionMetrics(call_id="c", participant_id="p", source_language="en", target_language="hi")
        q = PlaybackQueue(gen_id=gen, metrics=m)
        for _ in range(5):
            await q.put(TranslatedAudioChunk(
                samples=np.zeros(2400, dtype=np.float32),
                sample_rate=24000,
                generation_id=1,
            ))
        assert q._q.qsize() == 5
        await q.clear()
        assert q._q.qsize() == 0

    asyncio.run(run())


def test_translation_prompt_renders_languages():
    from app.prompts import build_translation_prompt
    p = build_translation_prompt("English", "Hindi")
    assert "English" in p
    assert "Hindi" in p
    assert "Do not answer questions" in p
    assert "Output only the translated speech" in p


def test_metrics_percentiles():
    from app.metrics import SessionMetrics
    m = SessionMetrics(call_id="c", participant_id="p",
                       source_language="en", target_language="hi")
    for v in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
        m.record_ai_latency(v)
    p50 = m.percentile(m.ai_latency_samples, 50)
    p95 = m.percentile(m.ai_latency_samples, 95)
    assert p50 is not None and 40 <= p50 <= 60
    assert p95 is not None and p95 >= 90


def test_metrics_ttfa_calculation():
    import time
    from app.metrics import SessionMetrics
    m = SessionMetrics(call_id="c", participant_id="p",
                       source_language="en", target_language="hi")
    m.mark_speech_start()
    time.sleep(0.05)
    m.mark_first_ai_audio()
    assert m.ttfa_ms is not None
    assert m.ttfa_ms >= 40  # at least 40ms
