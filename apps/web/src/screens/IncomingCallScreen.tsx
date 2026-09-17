import React, { useEffect, useRef, useState } from "react";
import { useCallStore } from "@/store/callStore";
import { acceptCall, listIncomingCalls, rejectCall } from "@/services/api";
import { Icon } from "@/components/icons";
import { ringtone } from "@/utils/ringtone";
import type { IncomingCall } from "@/services/api";

const handledCallIds = new Set<string>();

/**
 * Renders nothing visually unless there's an active incoming call.
 * Always polls /api/v1/calls/incoming while the user is IDLE on Home screen.
 */
export const IncomingCallPoller: React.FC = () => {
  const { authToken, callState, setIncomingCalls, setActiveIncomingCall, activeIncomingCall } =
    useCallStore();
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Manage incoming ringtone
  useEffect(() => {
    if (activeIncomingCall && callState === "IDLE") {
      ringtone.startIncomingRing();
    } else {
      ringtone.stop();
    }
    return () => {
      ringtone.stop();
    };
  }, [activeIncomingCall, callState]);

  // Poll for incoming calls every 2 seconds while IDLE
  useEffect(() => {
    if (!authToken) return;
    if (callState !== "IDLE") return;

    const poll = async () => {
      try {
        const calls = await listIncomingCalls(authToken);
        const validCalls = calls.filter((c) => !handledCallIds.has(c.call_id));
        setIncomingCalls(validCalls);
        // Auto-pick the most recent pending one if none active
        if (validCalls.length > 0 && !activeIncomingCall) {
          setActiveIncomingCall(validCalls[0]);
        } else if (validCalls.length === 0 && activeIncomingCall) {
          // No more incoming calls — caller cancelled?
          setActiveIncomingCall(null);
        }
      } catch (e) {
        // Silent fail — likely no auth
      }
    };
    poll();
    pollRef.current = setInterval(poll, 2000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [authToken, callState, activeIncomingCall, setIncomingCalls, setActiveIncomingCall]);

  if (!activeIncomingCall) return null;
  if (callState !== "IDLE") return null;

  return <IncomingCallBanner call={activeIncomingCall} />;
};

const IncomingCallBanner: React.FC<{ call: IncomingCall }> = ({ call }) => {
  const { authToken, setCallInfo, setActiveIncomingCall } = useCallStore();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleAccept = async () => {
    setBusy(true);
    setErr(null);
    handledCallIds.add(call.call_id);
    try {
      const res = await acceptCall(authToken!, call.call_id);
      setCallInfo({
        callId: res.call_id,
        roomName: res.room_name,
        livekitUrl: res.livekit_url,
        participantToken: res.participant_token,
        partnerId: call.caller_id,
        partnerName: call.caller_name || call.caller_email || call.caller_id,
      });
      // Clear the incoming call so the banner disappears
      setActiveIncomingCall(null);
    } catch (e: any) {
      setErr(e?.message || "Failed to accept call");
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async () => {
    setBusy(true);
    handledCallIds.add(call.call_id);
    try {
      await rejectCall(authToken!, call.call_id);
      setActiveIncomingCall(null);
    } catch (e: any) {
      setErr(e?.message || "Failed to reject call");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-x-0 top-0 z-50 p-4">
      <div className="mx-auto max-w-md rounded-2xl border border-brand-500/40 bg-slate-900/95 p-4 shadow-2xl backdrop-blur">
        <div className="flex items-center gap-3">
          <div className="relative flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-brand-600/20 ring-1 ring-brand-500/50">
            <Icon.Phone className="h-5 w-5 text-brand-400" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-xs uppercase tracking-wide text-brand-400">Incoming call</p>
            <p className="truncate text-base font-semibold">
              {call.caller_name || call.caller_email || call.caller_id.slice(0, 12)}
            </p>
            {call.caller_speak && call.caller_hear && (
              <p className="text-xs text-slate-400">
                They speak {call.caller_speak} → hear {call.caller_hear}
              </p>
            )}
          </div>
        </div>
        {err && <p className="mt-2 text-xs text-rose-400">{err}</p>}
        <div className="mt-3 flex gap-2">
          <button
            onClick={handleAccept}
            disabled={busy}
            className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-40"
          >
            <Icon.Phone className="h-4 w-4" />
            Accept
          </button>
          <button
            onClick={handleReject}
            disabled={busy}
            className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-rose-600 px-4 py-2 text-sm font-medium text-white hover:bg-rose-500 disabled:opacity-40"
          >
            <Icon.PhoneOff className="h-4 w-4" />
            Decline
          </button>
        </div>
      </div>
    </div>
  );
};
