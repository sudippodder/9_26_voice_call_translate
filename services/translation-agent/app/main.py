"""Translation Agent — main loop.

This process does NOT use LiveKit's server-side agent dispatch system
(which requires explicit dispatcher configuration on LiveKit Cloud).
Instead it polls a Redis list (`agent:join_queue`) for "join this room"
commands. The FastAPI backend pushes a command whenever a call is accepted.

When a command arrives, the agent spawns a new asyncio task that:
  1. Connects to the LiveKit room using the provided agent token
  2. Reads participant metadata (speak / hear language pairs)
  3. Creates two TranslationSession instances (one per direction)
  4. Subscribes to each user's mic track and pumps audio into the right session
  5. Publishes translated audio tracks back into the room

The agent process can handle many concurrent rooms — each room runs in its
own asyncio task. To scale horizontally, run more agent replicas.
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

import redis.asyncio as aioredis

# LiveKit realtime SDK — direct room connection (not the agents framework)
from livekit.rtc import (
    Room,
    RemoteParticipant,
    RemoteAudioTrack,
    TrackKind,
    AudioSource,
    AudioStream,
    LocalAudioTrack,
    AudioFrame as RTCAudioFrame,
    TrackPublishOptions,
    TrackSource,
)

from app.config import settings
from app.logging_setup import configure_logging, get_logger
from app.session import TranslationSession, TranslationSessionConfig

log = get_logger("agent_main")

# Track-name convention: the frontend subscribes only to tracks whose name
# matches `translated_audio_to_<my_user_id>`.
TRANSLATED_TRACK_PREFIX = "translated_audio_to_"


class RoomTranslationManager:
    """Manages one LiveKit room: subscribes to mics, runs two sessions, publishes translated tracks."""

    def __init__(self, room_name: str, livekit_url: str, agent_token: str,
                 participants: list[dict], call_id: str):
        self.room_name = room_name
        self.livekit_url = livekit_url
        self.agent_token = agent_token
        self.participants_meta = {p["participant_id"]: p for p in participants}
        self.call_id = call_id
        self.room: Optional[Room] = None
        self.sessions: dict[str, TranslationSession] = {}
        self.published_sources: dict[str, AudioSource] = {}
        self.consuming_track_sids: set[str] = set()
        self._lock = asyncio.Lock()
        self._closed = False

    async def run(self) -> None:
        """Connect to the room, set up sessions, and keep them alive until closed."""
        self.room = Room()

        # Wire up event handlers BEFORE connecting (synchronous callbacks spawning tasks)
        self.room.on(
            "track_subscribed",
            lambda track, publication, participant: asyncio.create_task(
                self._on_track_subscribed(track, publication, participant)
            ),
        )
        self.room.on(
            "participant_connected",
            lambda participant: asyncio.create_task(
                self._on_participant_connected(participant)
            ),
        )
        self.room.on(
            "participant_disconnected",
            lambda participant: asyncio.create_task(
                self._on_participant_disconnected(participant)
            ),
        )
        self.room.on(
            "disconnected",
            lambda *args: asyncio.create_task(self._on_disconnected(*args)),
        )

        try:
            log.info("agent_connecting_room",
                     room=self.room_name,
                     participants=list(self.participants_meta.keys()))
            await self.room.connect(self.livekit_url, self.agent_token)
            log.info("agent_connected_room", room=self.room_name)

            # Process already-present participants
            for p in list(self.room.remote_participants.values()):
                await self._register_participant(p)

            # Stay alive until the room is closed externally or stop() is called.
            while not self._closed:
                await asyncio.sleep(1.0)
        except Exception as e:  # noqa: BLE001
            log.error("agent_room_failed", room=self.room_name, error=str(e))
        finally:
            await self.cleanup()

    async def _register_participant(self, participant: RemoteParticipant) -> None:
        """Look up the participant's language pair and start a translation session."""
        async with self._lock:
            pid = participant.identity
            if pid in self.sessions:
                return  # Already registered

            meta = self.participants_meta.get(pid)
            if not meta:
                log.warning("agent_no_meta_for_participant", participant=pid)
                return

            # Find the OTHER participant (the listener — who hears the translated audio)
            listener_id = None
            for other_id in self.participants_meta.keys():
                if other_id != pid:
                    listener_id = other_id
                    break
            if not listener_id:
                log.warning("agent_no_listener_found", speaker=pid)
                return

            cfg = TranslationSessionConfig(
                call_id=self.call_id,
                participant_id=pid,
                listener_id=listener_id,
                source_language=meta["speak"],
                target_language=meta["hear"],
                input_track_sample_rate=48000,
                input_track_channels=1,
            )

            # Publish a translated audio track destined for the listener
            source = AudioSource(
                sample_rate=settings.audio_sample_rate,
                num_channels=settings.audio_channels,
            )
            track_name = f"{TRANSLATED_TRACK_PREFIX}{listener_id}"

            async def _on_frame(samples) -> None:
                try:
                    pcm16 = (samples.clip(-1, 1) * 32767.0).astype("int16").tobytes()
                    frame = RTCAudioFrame(
                        data=pcm16,
                        sample_rate=settings.audio_sample_rate,
                        num_channels=settings.audio_channels,
                        samples_per_channel=len(samples),
                    )
                    await source.capture_frame(frame)
                except Exception as e:  # noqa: BLE001
                    log.warning("source_capture_failed", error=str(e), listener=listener_id)

            try:
                local_track = LocalAudioTrack.create_audio_track(track_name, source)
                await self.room.local_participant.publish_track(
                    local_track,
                    options=TrackPublishOptions(
                        source=TrackSource.SOURCE_MICROPHONE,
                    ),
                )
                log.info("agent_published_track", track_name=track_name, room=self.room_name)
            except Exception as e:  # noqa: BLE001
                log.error("agent_publish_failed", track_name=track_name, error=str(e))
                return

            self.published_sources[listener_id] = source

            async def _on_transcript(event) -> None:
                try:
                    msg = {
                        "type": "transcript",
                        "speaker_id": pid,
                        "listener_id": listener_id,
                        "direction": "incoming",
                        "language": meta["hear"],
                        "text": event.text,
                        "is_final": event.is_final,
                    }
                    if self.room and self.room.local_participant:
                        await self.room.local_participant.publish_data(
                            payload=json.dumps(msg).encode(),
                            topic="transcript",
                        )
                except Exception as e:  # noqa: BLE001
                    log.debug("transcript_broadcast_failed", error=str(e))

            async def _on_input_transcript(event) -> None:
                """Publish what the speaker said in their source language."""
                try:
                    msg = {
                        "type": "transcript",
                        "speaker_id": pid,
                        "listener_id": listener_id,
                        "direction": "outgoing",
                        "language": meta["speak"],
                        "text": event.text,
                        "is_final": event.is_final,
                    }
                    if self.room and self.room.local_participant:
                        await self.room.local_participant.publish_data(
                            payload=json.dumps(msg).encode(),
                            topic="transcript",
                        )
                except Exception as e:  # noqa: BLE001
                    log.debug("input_transcript_broadcast_failed", error=str(e))

            session = TranslationSession(cfg, on_publish_frame=_on_frame, on_transcript=_on_transcript, on_input_transcript=_on_input_transcript)
            self.sessions[pid] = session
            try:
                await session.start()
                log.info("agent_session_started",
                         speaker=pid, listener=listener_id,
                         src=meta["speak"], tgt=meta["hear"])
            except Exception as e:  # noqa: BLE001
                log.error("agent_session_start_failed", speaker=pid, error=str(e))

            # Consume any already-published tracks
            for pub in list(participant.track_publications.values()):
                if pub.track and pub.track.kind == TrackKind.KIND_AUDIO:
                    track_sid = getattr(pub, "sid", str(id(pub.track)))
                    if track_sid not in self.consuming_track_sids:
                        self.consuming_track_sids.add(track_sid)
                        asyncio.create_task(
                            self._start_consuming(pub.track, pid),
                            name=f"consume-{pid}-{self.call_id}",
                        )

    async def _start_consuming(self, track: RemoteAudioTrack, pid: str) -> None:
        session = self.sessions.get(pid)
        if not session:
            return
        log.info("agent_consuming_audio_stream", participant=pid, room=self.room_name)
        stream = AudioStream(track)
        try:
            async for event in stream:
                if self._closed:
                    break
                try:
                    frame = event.frame
                    await session.ingest_audio(
                        bytes(frame.data),
                        sample_rate=frame.sample_rate,
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("agent_ingest_failed", error=str(e), participant=pid)
        except Exception as e:  # noqa: BLE001
            log.warning("agent_stream_ended", participant=pid, error=str(e))

    async def _on_track_subscribed(self, track, publication, participant: RemoteParticipant) -> None:
        """A user published their mic — start consuming it."""
        if track.kind != TrackKind.KIND_AUDIO:
            return
        pid = participant.identity
        session = self.sessions.get(pid)
        if session is None:
            # Maybe metadata arrived late — try registering now
            await self._register_participant(participant)
            session = self.sessions.get(pid)
        if session is None:
            log.warning("agent_no_session_for_participant", participant=pid)
            return

        log.info("agent_track_subscribed", participant=pid, room=self.room_name)
        track_sid = getattr(publication, "sid", str(id(track)))
        if track_sid in self.consuming_track_sids:
            return
        self.consuming_track_sids.add(track_sid)
        asyncio.create_task(self._start_consuming(track, pid), name=f"consume-{pid}-{self.call_id}")

    async def _on_participant_connected(self, participant: RemoteParticipant) -> None:
        log.info("agent_participant_connected", identity=participant.identity)
        await self._register_participant(participant)

    async def _on_participant_disconnected(self, participant: RemoteParticipant) -> None:
        pid = participant.identity
        log.info("agent_participant_disconnected", identity=pid)
        session = self.sessions.pop(pid, None)
        if session:
            await session.stop()

    async def _on_disconnected(self, *args, **kwargs) -> None:
        log.warning("agent_room_disconnected", room=self.room_name)
        for s in list(self.sessions.values()):
            await s.stop()
        self.sessions.clear()
        self._closed = True

    async def cleanup(self) -> None:
        for s in list(self.sessions.values()):
            try:
                await s.stop()
            except Exception:  # noqa: BLE001
                pass
        self.sessions.clear()
        if self.room:
            try:
                await self.room.disconnect()
            except Exception:  # noqa: BLE001
                pass
        log.info("agent_room_cleaned", room=self.room_name)


# ---------------------------------------------------------------------------
# Main loop — poll Redis for join commands, spawn a RoomTranslationManager per call
# ---------------------------------------------------------------------------

async def main() -> None:
    configure_logging()
    log = get_logger("main")
    log.info("agent_main_starting",
             livekit_url=settings.livekit_url,
             redis_url=settings.redis_url)

    if not settings.livekit_url or not settings.livekit_api_secret:
        log.error("agent_missing_config",
                  livekit_url=bool(settings.livekit_url),
                  api_secret=bool(settings.livekit_api_secret))
        return

    r = aioredis.from_url(settings.redis_url, decode_responses=True, socket_timeout=60, socket_connect_timeout=10)
    active_rooms: dict[str, asyncio.Task] = {}  # call_id → task

    log.info("agent_polling_queue", queue="agent:join_queue")
    while True:
        try:
            # BLPOP blocks until an item is available (or timeout)
            item = await r.brpop("agent:join_queue", timeout=30)
            if not item:
                # Periodically log that we're still alive
                log.debug("agent_idle", active_rooms=len(active_rooms))
                # Also clean up finished rooms
                finished = [cid for cid, t in active_rooms.items() if t.done()]
                for cid in finished:
                    active_rooms.pop(cid, None)
                    log.info("agent_room_task_done", call_id=cid)
                continue

            _, raw = item
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as e:
                log.error("agent_invalid_payload", error=str(e), raw=raw[:200])
                continue

            call_id = payload["call_id"]
            if call_id in active_rooms and not active_rooms[call_id].done():
                log.warning("agent_room_already_active", call_id=call_id)
                continue

            manager = RoomTranslationManager(
                room_name=payload["room_name"],
                livekit_url=payload["livekit_url"],
                agent_token=payload["agent_token"],
                participants=payload["participants"],
                call_id=call_id,
            )
            task = asyncio.create_task(manager.run(), name=f"room-{call_id}")
            active_rooms[call_id] = task
            log.info("agent_dispatched", call_id=call_id, room=payload["room_name"])

        except asyncio.CancelledError:
            log.info("agent_main_cancelled")
            break
        except Exception as e:  # noqa: BLE001
            log.error("agent_main_error", error=str(e))
            await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
