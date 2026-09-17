/**
 * useCallLifecycle — wires together the call store with the LiveKit room.
 *
 * Encapsulates:
 *  - Creating the call via API
 *  - Connecting the VoiceTranslatorRoom
 *  - Subscribing to metrics + transcript callbacks
 *  - Cleanup on unmount / call end
 */
import { useEffect, useRef } from "react";
import { useCallStore } from "@/store/callStore";
import { createCall, endCall } from "@/services/api";
import { VoiceTranslatorRoom } from "@/livekit/client";
import { ConnectionState } from "livekit-client";

export function useCallLifecycle() {
  const store = useCallStore();
  const roomRef = useRef<VoiceTranslatorRoom | null>(null);

  useEffect(() => {
    if (store.callState !== "CALLING") return;

    let cancelled = false;

    const lifecycle = async () => {
      try {
        const token = store.authToken;
        if (!token) throw new Error("Not authenticated");

        const calleeId =
          store.partnerId || `user_partner_${Math.random().toString(36).slice(2, 8)}`;
        const res = await createCall(token, {
          callee_id: calleeId,
          source_language: store.mySpeak,
          target_language: store.myHear,
        });

        if (cancelled) return;

        store.setCallInfo({
          callId: res.call_id,
          roomName: res.room_name,
          livekitUrl: res.livekit_url,
          participantToken: res.participant_token,
          partnerId: calleeId,
        });
        store.setCallState("CONNECTING");

        const room = new VoiceTranslatorRoom();
        roomRef.current = room;
        room.setCallbacks({
          onConnectionStateChanged: (s: ConnectionState) => {
            if (s === ConnectionState.Connected) {
              store.setCallState("ACTIVE");
              store.setTranslationState("LISTENING");
              store.updateDiagnostics({ ai_session_connected: true });
            } else if (s === ConnectionState.Reconnecting) {
              store.setCallState("RECONNECTING");
              store.updateDiagnostics({
                reconnect_count: store.diagnostics.reconnect_count + 1,
              });
            }
          },
          onReconnecting: () => store.setCallState("RECONNECTING"),
          onReconnected: () => store.setCallState("ACTIVE"),
          onDisconnected: () => store.setCallState("ENDED"),
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
            }
          },
        });

        await room.connect(res.livekit_url, res.participant_token, store.user?.id || "anon");
      } catch (e: any) {
        console.error("call_failed", e);
        store.setCallState("ENDED");
      }
    };

    lifecycle();

    return () => {
      cancelled = true;
      if (roomRef.current) {
        roomRef.current.disconnect().catch(() => {});
        roomRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store.callState === "CALLING"]);

  return {
    room: roomRef,
    endCall: async () => {
      try {
        if (store.callId && store.authToken) {
          await endCall(store.authToken, store.callId).catch(() => {});
        }
      } finally {
        if (roomRef.current) {
          await roomRef.current.disconnect().catch(() => {});
          roomRef.current = null;
        }
        store.resetCall();
      }
    },
  };
}
