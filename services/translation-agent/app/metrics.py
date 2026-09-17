"""Per-session metrics — TTFA, latency, stalls, interrupts, P50/P95/P99."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional

from app.logging_setup import get_logger

log = get_logger("metrics")


@dataclass
class SessionMetrics:
    call_id: str
    participant_id: str
    source_language: str
    target_language: str

    # Latency samples (ms)
    ttfa_ms: Optional[float] = None  # Time To First Audio
    speech_start_ts: Optional[float] = None
    first_ai_audio_ts: Optional[float] = None
    first_playback_ts: Optional[float] = None

    ai_latency_samples: Deque[float] = field(default_factory=lambda: deque(maxlen=1000))
    network_rtt_samples: Deque[float] = field(default_factory=lambda: deque(maxlen=500))
    jitter_samples: Deque[float] = field(default_factory=lambda: deque(maxlen=500))
    packet_loss_samples: Deque[float] = field(default_factory=lambda: deque(maxlen=500))

    # Counters
    interrupt_count: int = 0
    reconnect_count: int = 0
    audio_stall_count: int = 0
    audio_seconds: float = 0.0
    translation_seconds: float = 0.0
    generation_id_high_water: int = 0

    def mark_speech_start(self) -> None:
        self.speech_start_ts = time.monotonic()

    def mark_first_ai_audio(self) -> None:
        self.first_ai_audio_ts = time.monotonic()
        if self.speech_start_ts is not None and self.ttfa_ms is None:
            self.ttfa_ms = (self.first_ai_audio_ts - self.speech_start_ts) * 1000.0
            log.info(
                "translation_first_audio",
                call_id=self.call_id,
                participant_id=self.participant_id,
                source_language=self.source_language,
                target_language=self.target_language,
                latency_ms=round(self.ttfa_ms, 1),
            )

    def mark_first_playback(self) -> None:
        self.first_playback_ts = time.monotonic()

    def record_ai_latency(self, ms: float) -> None:
        self.ai_latency_samples.append(ms)

    def record_network(self, rtt_ms: float, jitter_ms: float, loss_pct: float) -> None:
        self.network_rtt_samples.append(rtt_ms)
        self.jitter_samples.append(jitter_ms)
        self.packet_loss_samples.append(loss_pct)

    def increment_interrupt(self) -> None:
        self.interrupt_count += 1

    def increment_reconnect(self) -> None:
        self.reconnect_count += 1

    def increment_stall(self) -> None:
        self.audio_stall_count += 1

    def bump_generation(self, gen: int) -> None:
        self.generation_id_high_water = max(self.generation_id_high_water, gen)

    def percentile(self, samples: Deque[float], p: float) -> Optional[float]:
        if not samples:
            return None
        arr = sorted(samples)
        idx = max(0, min(len(arr) - 1, int(len(arr) * p / 100.0)))
        return arr[idx]

    def snapshot(self) -> dict:
        return {
            "call_id": self.call_id,
            "participant_id": self.participant_id,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "ttfa_ms": self.ttfa_ms,
            "ai_latency_p50_ms": self.percentile(self.ai_latency_samples, 50),
            "ai_latency_p95_ms": self.percentile(self.ai_latency_samples, 95),
            "ai_latency_p99_ms": self.percentile(self.ai_latency_samples, 99),
            "rtt_p50_ms": self.percentile(self.network_rtt_samples, 50),
            "jitter_p50_ms": self.percentile(self.jitter_samples, 50),
            "packet_loss_p50_pct": self.percentile(self.packet_loss_samples, 50),
            "interrupt_count": self.interrupt_count,
            "reconnect_count": self.reconnect_count,
            "audio_stall_count": self.audio_stall_count,
            "audio_seconds": self.audio_seconds,
            "translation_seconds": self.translation_seconds,
            "generation_id_high_water": self.generation_id_high_water,
        }
