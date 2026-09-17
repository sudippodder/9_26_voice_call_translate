import {
  Room,
  RoomEvent,
  Track,
  ConnectionState,
  RemoteParticipant,
  RemoteAudioTrack,
} from "livekit-client";
import type { CallDiagnostics } from "@/types";

/**
 * Wrapper around the LiveKit client SDK that:
 *  - connects with a server-minted participant token
 *  - publishes the local microphone track with echo cancellation + noise suppression + AGC
 *  - subscribes ONLY to translated tracks published by the agent (never to other users' mics)
 *  - exposes typed callbacks for state, translation, and diagnostics
 *
 * Track-name contract with the agent:
 *   translated_audio_to_<participant_id>
 *
 * The original mic track of the OTHER user is NEVER played locally. This is
 * enforced here by filtering subscribed audio tracks to those whose name
 * starts with "translated_audio_to_".
 */

export const TRANSLATED_TRACK_PREFIX = "translated_audio_to_";

export interface LiveKitCallbacks {
  onConnectionStateChanged?: (state: ConnectionState) => void;
  onActiveSpeakersChanged?: (speakers: RemoteParticipant[]) => void;
  onTranslatedAudioSubscribed?: (track: RemoteAudioTrack, participantId: string) => void;
  onTranslatedAudioUnsubscribed?: (track: RemoteAudioTrack) => void;
  onParticipantConnected?: (p: RemoteParticipant) => void;
  onParticipantDisconnected?: (p: RemoteParticipant) => void;
  onReconnecting?: () => void;
  onReconnected?: () => void;
  onDisconnected?: () => void;
  onMetrics?: (metrics: Partial<CallDiagnostics>) => void;
  onDataMessage?: (msg: unknown) => void;
}

export class VoiceTranslatorRoom {
  public room: Room;
  private callbacks: LiveKitCallbacks = {};
  private metricsTimer: ReturnType<typeof setInterval> | null = null;

  constructor() {
    this.room = new Room({
      // Use LiveKit's adaptive streaming — Opus audio with automatic network handling.
      adaptiveStream: true,
      dynacast: true,
      audioCaptureDefaults: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
      publishDefaults: {
        dtx: true,
      },
    });

    this._wireEvents();
  }

  public myUserId: string = "";

  setCallbacks(cb: LiveKitCallbacks) {
    this.callbacks = cb;
  }

  // ---------------------------------------------------------------------------
  // Connect / publish mic
  // ---------------------------------------------------------------------------
  async connect(url: string, token: string, myUserId: string) {
    this.myUserId = myUserId;
    await this.room.connect(url, token, {
      autoSubscribe: true,
    });

    if (this.room.localParticipant?.identity) {
      this.myUserId = this.room.localParticipant.identity;
    }

    // Publish local mic
    try {
      await this.room.localParticipant.setMicrophoneEnabled(true, {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      });
    } catch (err) {
      console.error("[livekit] failed to enable microphone:", err);
      throw err;
    }

    // Attempt to unlock audio playback immediately
    try {
      await this.room.startAudio();
    } catch {
      /* ignore audio unlock error until user interaction */
    }

    // Start metrics polling
    this.metricsTimer = setInterval(() => this._reportMetrics(), 2_000);
  }

  getLocalAudioTrack(): MediaStreamTrack | null {
    if (!this.room.localParticipant) return null;
    for (const pub of this.room.localParticipant.getTrackPublications().values()) {
      if (pub.track?.kind === Track.Kind.Audio) {
        return pub.track.mediaStreamTrack || null;
      }
    }
    return null;
  }

  // ---------------------------------------------------------------------------
  // Disconnect / cleanup
  // ---------------------------------------------------------------------------
  async disconnect() {
    if (this.metricsTimer) {
      clearInterval(this.metricsTimer);
      this.metricsTimer = null;
    }
    await this.room.disconnect();
  }

  toggleMute(muted: boolean) {
    this.room.localParticipant.setMicrophoneEnabled(!muted).catch((e) => {
      console.warn("[livekit] toggleMute error:", e);
    });
  }

  // ---------------------------------------------------------------------------
  // Subscribe to audio tracks
  // ---------------------------------------------------------------------------
  private _wireEvents() {
    this.room
      .on(RoomEvent.ConnectionStateChanged, (state: ConnectionState) => {
        this.callbacks.onConnectionStateChanged?.(state);
        if (state === ConnectionState.Reconnecting) {
          this.callbacks.onReconnecting?.();
        }
        if (state === ConnectionState.Connected) {
          if (this.room.localParticipant?.identity) {
            this.myUserId = this.room.localParticipant.identity;
          }
          this.callbacks.onReconnected?.();
        }
        if (state === ConnectionState.Disconnected) {
          this.callbacks.onDisconnected?.();
        }
      })
      .on(RoomEvent.ParticipantConnected, (p: RemoteParticipant) => {
        this.callbacks.onParticipantConnected?.(p);
      })
      .on(RoomEvent.ParticipantDisconnected, (p: RemoteParticipant) => {
        this.callbacks.onParticipantDisconnected?.(p);
      })
      .on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        this.callbacks.onActiveSpeakersChanged?.(speakers as RemoteParticipant[]);
      })
      .on(RoomEvent.TrackSubscribed, (track, pub, participant: RemoteParticipant) => {
        if (track.kind !== Track.Kind.Audio) return;
        const audioTrack = track as RemoteAudioTrack;
        const name = pub.trackName || "";
        const localId = this.room.localParticipant?.identity || this.myUserId;

        // If track name starts with translated_audio_to_, ensure it is destined for this user
        if (name.startsWith(TRANSLATED_TRACK_PREFIX)) {
          const targetId = name.replace(TRANSLATED_TRACK_PREFIX, "");
          if (localId && targetId && targetId !== localId && localId !== "anon") {
            // Track is intended for the other user in the room, detach and ignore
            audioTrack.detach();
            return;
          }
        }

        // Ensure Room audio playback is unlocked
        if (!this.room.canPlaybackAudio) {
          this.room.startAudio().catch((e) => console.warn("[livekit] startAudio:", e));
        }

        // Subscribe and play through <audio> element automatically.
        const targetEl = this._ensureAudioSink();
        targetEl.muted = false;
        audioTrack.attach(targetEl);
        audioTrack.attach(); // Also let LiveKit create an element attached to DOM
        targetEl.play().catch((err) => {
          console.warn("[livekit] audio autoplay blocked by browser, trying startAudio:", err);
          this.room.startAudio().catch(() => {});
        });
        this.callbacks.onTranslatedAudioSubscribed?.(audioTrack, participant.identity);
      })
      .on(RoomEvent.TrackUnsubscribed, (track) => {
        if (track.kind !== Track.Kind.Audio) return;
        const audioTrack = track as RemoteAudioTrack;
        audioTrack.detach();
        this.callbacks.onTranslatedAudioUnsubscribed?.(audioTrack);
      })
      .on(RoomEvent.DataReceived, (payload, _participant, _, topic) => {
        try {
          const msg = JSON.parse(new TextDecoder().decode(payload));
          if (topic === "transcript") {
            this.callbacks.onDataMessage?.(msg);
          }
        } catch {
          /* ignore malformed */
        }
      });
  }

  // ---------------------------------------------------------------------------
  // Hidden <audio> element the remote translated track attaches to
  // ---------------------------------------------------------------------------
  private _audioEl: HTMLAudioElement | null = null;

  private _ensureAudioSink(): HTMLAudioElement {
    if (!this._audioEl) {
      const el = document.createElement("audio");
      el.autoplay = true;
      el.setAttribute("playsinline", "true");
      el.style.position = "fixed";
      el.style.top = "-9999px";
      el.style.left = "-9999px";
      el.style.opacity = "0";
      el.style.pointerEvents = "none";
      document.body.appendChild(el);
      this._audioEl = el;
    }
    return this._audioEl;
  }

  setSpeakerEnabled(enabled: boolean) {
    if (this._audioEl) {
      this._audioEl.muted = !enabled;
    }
  }

  // ---------------------------------------------------------------------------
  // Metrics
  // ---------------------------------------------------------------------------
  private _reportMetrics() {
    // Aggregate stats across all remote participants.
    let rtt = 0, jitter = 0, loss = 0, n = 0;
    for (const p of this.room.remoteParticipants.values()) {
      for (const pub of p.getTrackPublications().values()) {
        // Use any to bypass the strict Track type — the runtime API supports
        // .currentStats on RemoteAudioTrack but it isn't in the public types yet.
        const stats: any = (pub.track as any)?.currentStats;
        if (!stats) continue;
        if (typeof stats.rtt === "number") { rtt += stats.rtt; n += 1; }
        if (typeof stats.jitter === "number") jitter += stats.jitter;
        if (typeof stats.packetsLost === "number" && typeof stats.packetsReceived === "number") {
          const total = stats.packetsLost + stats.packetsReceived;
          if (total > 0) loss += (stats.packetsLost / total) * 100;
        }
      }
    }
    if (n > 0) {
      const rttAvg = rtt / n;
      const jitterAvg = jitter / n;
      const lossAvg = loss / n;
      const quality =
        lossAvg > 5 || rttAvg > 400 ? "poor" : lossAvg > 1 || rttAvg > 150 ? "fair" : "good";
      this.callbacks.onMetrics?.({
        rtt_ms: Math.round(rttAvg),
        jitter_ms: Math.round(jitterAvg),
        packet_loss_pct: Number(lossAvg.toFixed(2)),
        connection_quality: quality as CallDiagnostics["connection_quality"],
      });
    }
  }
}
