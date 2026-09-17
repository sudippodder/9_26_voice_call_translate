import React, { useEffect, useRef, useState } from "react";
import { useCallStore } from "@/store/callStore";
import { createCall, endCall, getCallStatus } from "@/services/api";
import { VoiceTranslatorRoom } from "@/livekit/client";
import { CallButton } from "@/components/CallButton";
import { SoundTestButton } from "@/components/SoundTestButton";
import { AudioVisualizer } from "@/components/AudioVisualizer";
import { DiagnosticsPanel } from "@/components/DiagnosticsPanel";
import { Icon } from "@/components/icons";
import { ringtone } from "@/utils/ringtone";
import { ConnectionState, RemoteParticipant } from "livekit-client";

/**
 * Call screen handles the full lifecycle:
 *   CALLING (caller created call, waiting for receiver to accept)
 *      ↓ (poll call status — when receiver accepts, status becomes "active")
 *   CONNECTING (we have a LiveKit token, connecting room)
 *      ↓
 *   ACTIVE (room connected, translation running)
 *      ↓
 *   ENDED
 *
 * Receiver flow:
 *   The IncomingCallPoller on the Home screen accepts the call → setCallInfo
 *   → triggers this screen directly with state CONNECTING.
 */
export const CallScreen: React.FC = () => {
  const store = useCallStore();
  const roomRef = useRef<VoiceTranslatorRoom | null>(null);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [callErr, setCallErr] = useState<string | null>(null);
  const [localTrack, setLocalTrack] = useState<MediaStreamTrack | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // -------------------------------------------------------------
  // Ringtone management: Outgoing ringtone while in CALLING state
  // -------------------------------------------------------------
  useEffect(() => {
    if (store.callState === "CALLING") {
      ringtone.startOutgoingRing();
    } else {
      ringtone.stop();
    }
    return () => {
      ringtone.stop();
    };
  }, [store.callState]);

  // -------------------------------------------------------------
  // Unlock Web Audio playback on any user touch/click on screen
  // -------------------------------------------------------------
  useEffect(() => {
    const unlockAudio = () => {
      if (roomRef.current?.room && !roomRef.current.room.canPlaybackAudio) {
        roomRef.current.room.startAudio().catch(() => {});
      }
    };
    window.addEventListener("click", unlockAudio);
    window.addEventListener("touchstart", unlockAudio);
    return () => {
      window.removeEventListener("click", unlockAudio);
      window.removeEventListener("touchstart", unlockAudio);
    };
  }, []);

  // -------------------------------------------------------------
  // Caller creation & status polling
  // -------------------------------------------------------------
  useEffect(() => {
    if (store.callState !== "CALLING") return;
    if (!store.authToken || !store.selectedCallee) {
      store.resetCall();
      return;
    }

    let cancelled = false;
    const start = async () => {
      try {
        setCallErr(null);
        const res = await createCall(store.authToken!, {
          callee_id: store.selectedCallee!.id,
          source_language: store.mySpeak,
          target_language: store.myHear,
        });

        if (cancelled) return;

        const partnerName =
          store.selectedCallee!.display_name || store.selectedCallee!.email || store.selectedCallee!.id;
        
        // Preserve CALLING state so ringtone and polling continue
        store.setCallInfo({
          callId: res.call_id,
          roomName: res.room_name,
          livekitUrl: res.livekit_url,
          participantToken: res.participant_token,
          partnerId: store.selectedCallee!.id,
          partnerName,
          callState: "CALLING",
        });

        // The caller connects to the LiveKit room immediately and waits for receiver to accept
        await connectRoom(res.livekit_url, res.participant_token);
      } catch (e: any) {
        setCallErr(e?.message || "Failed to start call");
      }
    };
    start();

    // Poll the call status — when receiver accepts, status becomes "active"
    pollRef.current = setInterval(async () => {
      const currentCallId = useCallStore.getState().callId;
      const currentToken = useCallStore.getState().authToken;
      const currentCallState = useCallStore.getState().callState;
      if (!currentToken || !currentCallId) return;

      try {
        const c = await getCallStatus(currentToken, currentCallId);
        if (c.status === "active") {
          if (currentCallState === "CALLING") {
            store.setCallState("ACTIVE");
          }
        } else if (c.status === "rejected" || c.status === "ended" || c.status === "missed") {
          setCallErr(`Call ${c.status}`);
          if (pollRef.current) clearInterval(pollRef.current);
          if (roomRef.current) await roomRef.current.disconnect().catch(() => {});
          store.resetCall();
        }
      } catch {
        /* ignore poll errors */
      }
    }, 1500);

    return () => {
      cancelled = true;
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.callState === "CALLING"]);

  // -------------------------------------------------------------
  // Receiver connect when callState becomes CONNECTING
  // -------------------------------------------------------------
  useEffect(() => {
    if (store.callState !== "CONNECTING") return;
    if (!store.livekitUrl || !store.participantToken) return;
    if (roomRef.current) return;

    const connect = async () => {
      try {
        await connectRoom(store.livekitUrl!, store.participantToken!);
      } catch (e: any) {
        setCallErr(e?.message || "Failed to connect");
      }
    };
    connect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.callState === "CONNECTING"]);

  // -------------------------------------------------------------
  // Active call status poller: auto-disconnect if partner ends call
  // -------------------------------------------------------------
  useEffect(() => {
    if (store.callState !== "ACTIVE") return;
    const pollActive = setInterval(async () => {
      const currentCallId = useCallStore.getState().callId;
      const currentToken = useCallStore.getState().authToken;
      if (!currentToken || !currentCallId) return;
      try {
        const c = await getCallStatus(currentToken, currentCallId);
        if (c.status === "ended" || c.status === "rejected" || c.status === "missed") {
          clearInterval(pollActive);
          if (roomRef.current) {
            await roomRef.current.disconnect().catch(() => {});
            roomRef.current = null;
          }
          setLocalTrack(null);
          store.resetCall();
        }
      } catch {
        /* ignore */
      }
    }, 1500);
    return () => clearInterval(pollActive);
  }, [store.callState]);

  // Shared room connect helper
  const connectRoom = async (url: string, token: string) => {
    const room = new VoiceTranslatorRoom();
    roomRef.current = room;
    room.setCallbacks({
      onConnectionStateChanged: (s: ConnectionState) => {
        if (s === ConnectionState.Connected) {
          // If we are connecting as receiver or already active, set ACTIVE.
          // If caller is still waiting for callee to accept (CALLING), keep CALLING!
          if (useCallStore.getState().callState !== "CALLING") {
            store.setCallState("ACTIVE");
          }
          store.setTranslationState("LISTENING");
          store.updateDiagnostics({ ai_session_connected: true });
        } else if (s === ConnectionState.Reconnecting) {
          store.setCallState("RECONNECTING");
          store.updateDiagnostics({ reconnect_count: store.diagnostics.reconnect_count + 1 });
        }
      },
      onReconnecting: () => store.setCallState("RECONNECTING"),
      onReconnected: () => store.setCallState("ACTIVE"),
      onParticipantDisconnected: (p: RemoteParticipant) => {
        // If the other person hung up or disconnected, end the call locally
        if (p.identity !== "translator_agent") {
          handleEnd();
        }
      },
      onDisconnected: () => {
        if (roomRef.current) {
          roomRef.current = null;
        }
        setLocalTrack(null);
        store.resetCall();
      },
      onTranslatedAudioSubscribed: () => store.setTranslationState("PLAYING"),
      onMetrics: (m) => store.updateDiagnostics(m),
      onDataMessage: (msg: any) => {
        if (msg?.type === "transcript") {
          store.addTranscript({
            direction: msg.direction,
            language: msg.language,
            text: msg.text,
            timestamp: Date.now(),
            is_final: msg.is_final ?? true,
          });
          // Auto-open transcript panel on first transcript
          if (!transcriptOpen) {
            setTranscriptOpen(true);
          }
        }
      },
    });

    await room.connect(url, token, store.user?.id || "");
    const track = room.getLocalAudioTrack();
    if (track) {
      setLocalTrack(track);
    }
  };

  // -------------------------------------------------------------
  // Mute / Speaker toggles
  // -------------------------------------------------------------
  useEffect(() => {
    roomRef.current?.toggleMute(store.micMuted);
  }, [store.micMuted]);

  useEffect(() => {
    roomRef.current?.setSpeakerEnabled(!store.speakerMuted);
  }, [store.speakerMuted]);

  // -------------------------------------------------------------
  // End call handler
  // -------------------------------------------------------------
  const handleEnd = async () => {
    try {
      if (store.callId && store.authToken) {
        await endCall(store.authToken, store.callId).catch(() => {});
      }
    } finally {
      if (pollRef.current) clearInterval(pollRef.current);
      if (roomRef.current) {
        await roomRef.current.disconnect().catch(() => {});
        roomRef.current = null;
      }
      setLocalTrack(null);
      store.resetCall();
    }
  };

  // Cancel an outgoing call (caller side, while still ringing)
  const handleCancel = async () => {
    if (pollRef.current) clearInterval(pollRef.current);
    await handleEnd();
  };

  // -------------------------------------------------------------
  // Render
  // -------------------------------------------------------------
  const statusText = (() => {
    switch (store.callState) {
      case "CALLING": return `Calling ${store.partnerName || ""}…`;
      case "CONNECTING": return "Connecting…";
      case "ACTIVE":
        return store.translationState === "PLAYING"
          ? "Playing translation"
          : store.translationState === "TRANSLATING"
          ? "Translating"
          : "Listening";
      case "RECONNECTING": return "Reconnecting…";
      case "ENDED": return "Call ended";
      default: return "Idle";
    }
  })();

  const partnerLabel = store.partnerName || store.partnerId?.slice(0, 10) || "Partner";
  const speakName = store.languages.find((l) => l.code === store.mySpeak)?.name || store.mySpeak;
  const hearName = store.languages.find((l) => l.code === store.myHear)?.native_name || store.myHear;

  return (
    <div className="relative flex min-h-screen flex-col items-center justify-between p-6">
      <header className="w-full max-w-md space-y-2 pt-4 text-center">
        <div className="inline-flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900/80 px-3 py-1 text-xs">
          <span
            className={`h-2 w-2 rounded-full ${
              store.callState === "ACTIVE"
                ? "bg-emerald-400 animate-pulse"
                : store.callState === "RECONNECTING"
                ? "bg-amber-400 animate-pulse"
                : store.callState === "CALLING"
                ? "bg-brand-400 animate-pulse"
                : "bg-slate-500"
            }`}
          />
          {statusText}
        </div>
        <h2 className="mt-3 text-2xl font-semibold">{partnerLabel}</h2>
        <p className="text-sm text-slate-400">
          {speakName} <span className="text-brand-400">→</span> {hearName}
        </p>
        <div className="pt-1 flex justify-center">
          <SoundTestButton size="sm" variant="minimal" />
        </div>
        {callErr && <p className="text-xs text-rose-400">{callErr}</p>}
      </header>

      {/* Center visualizer & speech status */}
      <div className="flex flex-1 items-center justify-center w-full">
        {store.callState === "ACTIVE" && (
          <AudioVisualizer
            isActive={store.callState === "ACTIVE"}
            isSpeaking={
              store.translationState === "TRANSLATING" || store.translationState === "PLAYING"
            }
            statusText={statusText}
            localTrack={localTrack}
            latestTranscript={
              store.transcripts.length > 0
                ? store.transcripts[store.transcripts.length - 1].text
                : ""
            }
          />
        )}
        {store.callState === "CALLING" && (
          <div className="flex flex-col items-center gap-4 text-slate-300">
            <div className="relative flex h-24 w-24 items-center justify-center">
              <div className="pulse-ring absolute inset-0 rounded-full text-brand-500" />
              <div className="relative flex h-16 w-16 items-center justify-center rounded-full bg-slate-900 ring-1 ring-slate-700">
                <Icon.Phone className="h-7 w-7 text-brand-400" />
              </div>
            </div>
            <p className="text-sm">Ringing…</p>
          </div>
        )}
        {(store.callState === "CONNECTING" || store.callState === "RECONNECTING") && (
          <div className="flex flex-col items-center gap-3 text-slate-400">
            <svg className="h-10 w-10 animate-spin text-brand-500" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4z" />
            </svg>
            <p className="text-sm">
              {store.callState === "RECONNECTING" ? "Reconnecting…" : "Establishing connection…"}
            </p>
          </div>
        )}
      </div>

      {/* Transcript */}
      {transcriptOpen && (
        <div className="absolute left-1/2 top-24 z-30 w-[90%] max-w-md -translate-x-1/2 rounded-xl border border-slate-700 bg-slate-900/95 p-3 backdrop-blur">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">Live transcript</span>
            <button
              onClick={() => setTranscriptOpen(false)}
              className="text-slate-500 hover:text-slate-200"
            >
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
          <div className="max-h-60 space-y-2 overflow-y-auto text-sm">
            {store.transcripts.length === 0 && <p className="text-xs text-slate-500">No transcript yet.</p>}
            {store.transcripts.map((t) => (
              <div
                key={t.id}
                className={`rounded-lg p-2 ${
                  t.direction === "outgoing" ? "bg-slate-800 text-right" : "bg-brand-600/15 text-left"
                }`}
              >
                <div className="text-[10px] uppercase tracking-wide text-slate-500">{t.language}</div>
                <div className="mt-0.5">{t.text}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="w-full max-w-md">
        <DiagnosticsPanel />
      </div>

      <footer className="w-full max-w-md pb-6">
        <div className="flex items-center justify-center gap-4">
          <CallButton
            icon={store.micMuted ? "MicOff" : "Mic"}
            label={store.micMuted ? "Unmute" : "Mute"}
            onClick={store.toggleMute}
            active={store.micMuted}
            disabled={store.callState !== "ACTIVE"}
          />
          <CallButton
            icon={store.speakerMuted ? "VolumeMute" : "Volume"}
            label="Speaker"
            onClick={store.toggleSpeaker}
            active={store.speakerMuted}
            disabled={store.callState !== "ACTIVE"}
          />
          <CallButton
            icon="Activity"
            label="Stats"
            onClick={store.toggleDiagnostics}
            active={store.showDiagnostics}
          />
          <CallButton
            icon="Settings"
            label="CC"
            onClick={() => {
              setTranscriptOpen((v) => !v);
              store.toggleTranscript();
            }}
            active={transcriptOpen}
          />
          {store.callState === "CALLING" ? (
            <CallButton icon="PhoneOff" label="Cancel" onClick={handleCancel} danger />
          ) : (
            <CallButton icon="PhoneOff" label="End" onClick={handleEnd} danger />
          )}
        </div>
      </footer>
    </div>
  );
};
