"""LiveKit Agent entrypoint.

Joins a call's LiveKit room, subscribes to each user's microphone track,
creates TWO TranslationSessions (one per direction), and publishes the
translated audio back into the room on dedicated tracks.

Critical media-architecture notes:
- Original mic tracks are NEVER played to the other user directly.
- The agent subscribes to user mics, translates, and publishes translated tracks.
- Frontend subscribes ONLY to translated tracks (filtered by track name).
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from livekit import api
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
)
from livekit.rtc import (
    AudioFrame as RTCAudioFrame,
    AudioSource,
    TrackPublishOptions,
    TrackKind,
    TrackSource,
)

from app.config import settings
from app.logging_setup import configure_logging, get_logger
from app.session import TranslationSession, TranslationSessionConfig

log = get_logger("agent")

# Track name convention:
#   translated_audio_to_<participant_id>
# Frontend subscribes to the track whose suffix matches its own user_id.
TRANSLATED_TRACK_PREFIX = "translated_audio_to_"


class TranslationAgent:
    """A LiveKit Agent that runs bidirectional realtime translation in a room."""

    def __init__(self, ctx: Optional[JobContext] = None) -> None:
        self.ctx = ctx
        self.sessions: dict[str, TranslationSession] = {}  # participant_id → session
        self.published_sources: dict[str, AudioSource] = {}
        self.participant_languages: dict[str, dict] = {}  # participant_id → {speak, hear}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ room joined
    async def on_enter(self) -> None:
        ctx = self.ctx
        log.info("agent_entered_room",
                 room=ctx.room.name,
                 participants=[p.identity for p in ctx.room.remote_participants.values()])

        # Subscribe to all existing + future participants' mic tracks.
        # AutoSubscribe is already configured in WorkerOptions; we attach
        # track-subscribed handlers here.
        ctx.room.on("track_subscribed", self._on_track_subscribed)
        ctx.room.on("participant_connected", self._on_participant_connected)
        ctx.room.on("participant_disconnected", self._on_participant_disconnected)
        ctx.room.on("disconnected", self._on_room_disconnected)

        # Process already-connected participants
        for p in ctx.room.remote_participants.values():
            await self._register_participant(p)

    # ------------------------------------------------------------------ participant discovery
    async def _register_participant(self, participant) -> None:
        """Look up the participant's language pair from call state (Redis / metadata).

        For V1 simplicity, we expect each participant to publish a Data track
        message declaring their language pair (speak, hear) when they join.
        If absent, we infer from call_id mapping fetched via the API at job start.
        """
        # In production, the API passes this via job metadata (parsing here):
        meta = self._parse_participant_meta(participant)
        if meta:
            self.participant_languages[participant.identity] = meta
            await self._ensure_session(participant, meta)

    def _parse_participant_meta(self, participant) -> Optional[dict]:
        """Read language pair from participant metadata (set when FastAPI minted token)."""
        md = participant.metadata or ""
        if not md:
            return None
        try:
            data = json.loads(md)
            return {"speak": data["speak"], "hear": data["hear"]}
        except Exception:  # noqa: BLE001
            return None

    async def _ensure_session(self, participant, meta: dict) -> None:
        async with self._lock:
            pid = participant.identity
            if pid in self.sessions:
                return
            # Find the OTHER participant (the listener)
            other = None
            for op in self.ctx.room.remote_participants.values():
                if op.identity != pid:
                    other = op
                    break
            listener_id = other.identity if other else f"{pid}_listener"

            cfg = TranslationSessionConfig(
                call_id=self._call_id(),
                participant_id=pid,
                listener_id=listener_id,
                source_language=meta["speak"],
                target_language=meta["hear"],
            )
            # Publish a translated audio track for the listener.
            source = AudioSource(
                sample_rate=settings.audio_sample_rate,
                num_channels=settings.audio_channels,
            )
            track_name = f"{TRANSLATED_TRACK_PREFIX}{listener_id}"
            # Cleanup old track if any, then publish
            await self.ctx.room.local_participant.publish_track(
                source=source,
                options=TrackPublishOptions(
                    name=track_name,
                    source=TrackSource.SOURCE_MICROPHONE,
                ),
            )
            self.published_sources[listener_id] = source

            async def _on_frame(samples) -> None:
                # Push float32 mono samples into the LiveKit AudioSource.
                pcm16 = (samples.clip(-1, 1) * 32767.0).astype("int16").tobytes()
                frame = RTCAudioFrame(
                    data=pcm16,
                    sample_rate=settings.audio_sample_rate,
                    num_channels=settings.audio_channels,
                    samples_per_channel=len(samples),
                )
                try:
                    await source.capture_frame(frame)
                except Exception as e:  # noqa: BLE001
                    log.warning("source_capture_failed", error=str(e), listener=listener_id)

            session = TranslationSession(cfg, on_publish_frame=_on_frame)
            self.sessions[pid] = session
            await session.start()
            log.info("session_created",
                     speaker=pid,
                     listener=listener_id,
                     src=meta["speak"], tgt=meta["hear"])

    def _call_id(self) -> str:
        # Job args contain the call_id; we read from the request passed via cli.run.
        return getattr(self, "_call_id_value", "unknown")

    # ------------------------------------------------------------------ track events
    async def _on_track_subscribed(self, track, publication, participant) -> None:
        """A user published their microphone track — start consuming it."""
        if track.kind != TrackKind.KIND_AUDIO:
            return
        pid = participant.identity
        session = self.sessions.get(pid)
        if session is None:
            # Try to register / start session now (metadata may have arrived late)
            await self._register_participant(participant)
            session = self.sessions.get(pid)
        if session is None:
            log.warning("no_session_for_participant", participant=pid)
            return

        log.info("track_subscribed", participant=pid, track=publication.name)

        async def _consume():
            stream = track.stream()
            async for frame in stream:
                # LiveKit AudioFrame: data (pcm16 bytes), sample_rate, num_channels
                try:
                    await session.ingest_audio(
                        frame.data,
                        sample_rate=frame.sample_rate,
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("ingest_audio_failed", error=str(e), participant=pid)

        asyncio.create_task(_consume(), name=f"consume-{pid}")

    async def _on_participant_connected(self, participant) -> None:
        await self._register_participant(participant)

    async def _on_participant_disconnected(self, participant) -> None:
        pid = participant.identity
        session = self.sessions.pop(pid, None)
        if session:
            await session.stop()

    async def _on_room_disconnected(self, *args, **kwargs) -> None:
        log.warning("agent_room_disconnected")
        for s in list(self.sessions.values()):
            await s.stop()
        self.sessions.clear()


# ---------------------------------------------------------------------------
# Job prelude — fetch call metadata + LiveKit token, then enter the room.
# ---------------------------------------------------------------------------

async def prelude(ctx: JobContext) -> None:
    """Look up call metadata from job args, set up the agent's per-call state."""
    configure_logging()
    # The FastAPI layer dispatches a job with `metadata = {call_id, languages}`.
    md_raw = getattr(ctx.job, "metadata", None) or "{}"
    try:
        md = json.loads(md_raw)
    except json.JSONDecodeError:
        md = {}
    call_id = md.get("call_id", "unknown")
    log.info("agent_prelude", call_id=call_id, metadata=md)
    await ctx.connect()


async def agent_entrypoint(ctx: JobContext) -> None:
    """LiveKit agents framework entrypoint."""
    await prelude(ctx)
    agent = TranslationAgent(ctx)
    # Stash call_id on the agent for logging
    try:
        md_raw = getattr(ctx.job, "metadata", None) or "{}"
        md = json.loads(md_raw)
        agent._call_id_value = md.get("call_id", "unknown")
    except Exception:  # noqa: BLE001
        agent._call_id_value = "unknown"
    await agent.on_enter()


if __name__ == "__main__":
    # Run via `python -m app.main` — uses LiveKit agents CLI to dispatch workers.
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=agent_entrypoint,
            prewarm_fnc=lambda proc: None,
            # The agent subscribes to user mic tracks; we want it to auto-subscribe.
            autosubscribe=AutoSubscribe.AUDIO_ONLY,
            # One agent process per room (so two sessions per process).
            num_idle_processes=2,
        )
    )
