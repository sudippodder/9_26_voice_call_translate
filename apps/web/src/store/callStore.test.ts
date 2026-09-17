import { describe, it, expect, beforeEach } from "vitest";
import { useCallStore } from "@/store/callStore";

describe("callStore", () => {
  beforeEach(() => {
    useCallStore.getState().resetCall();
    useCallStore.setState({ authToken: null, user: null });
  });

  it("starts in IDLE state", () => {
    expect(useCallStore.getState().callState).toBe("IDLE");
  });

  it("sets languages and language selections", () => {
    useCallStore.getState().setLanguages([
      { code: "en", name: "English", native_name: "English" },
      { code: "hi", name: "Hindi", native_name: "हिन्दी" },
    ]);
    useCallStore.getState().setSpeak("en");
    useCallStore.getState().setHear("hi");
    const s = useCallStore.getState();
    expect(s.mySpeak).toBe("en");
    expect(s.myHear).toBe("hi");
    expect(s.languages).toHaveLength(2);
  });

  it("toggles mute", () => {
    expect(useCallStore.getState().micMuted).toBe(false);
    useCallStore.getState().toggleMute();
    expect(useCallStore.getState().micMuted).toBe(true);
    useCallStore.getState().toggleMute();
    expect(useCallStore.getState().micMuted).toBe(false);
  });

  it("transitions through call states", () => {
    useCallStore.getState().setCallState("CALLING");
    expect(useCallStore.getState().callState).toBe("CALLING");
    useCallStore.getState().setCallState("CONNECTING");
    expect(useCallStore.getState().callState).toBe("CONNECTING");
    useCallStore.getState().setCallState("ACTIVE");
    expect(useCallStore.getState().callState).toBe("ACTIVE");
    useCallStore.getState().setCallState("ENDED");
    expect(useCallStore.getState().callState).toBe("ENDED");
  });

  it("setCallInfo populates roomName and partnerId", () => {
    useCallStore.getState().setCallInfo({
      callId: "call_1",
      roomName: "call_1",
      livekitUrl: "wss://x",
      participantToken: "tok",
      partnerId: "user_2",
    });
    const s = useCallStore.getState();
    expect(s.callId).toBe("call_1");
    expect(s.roomName).toBe("call_1");
    expect(s.partnerId).toBe("user_2");
    expect(s.callState).toBe("CONNECTING");
  });

  it("resetCall returns to IDLE with cleared info", () => {
    useCallStore.getState().setCallInfo({
      callId: "call_1",
      roomName: "call_1",
      livekitUrl: "wss://x",
      participantToken: "tok",
      partnerId: "user_2",
    });
    useCallStore.getState().resetCall();
    const s = useCallStore.getState();
    expect(s.callState).toBe("IDLE");
    expect(s.callId).toBeNull();
    expect(s.partnerId).toBeNull();
  });

  it("updates diagnostics partially", () => {
    useCallStore.getState().updateDiagnostics({ rtt_ms: 54, packet_loss_pct: 0.3 });
    const d = useCallStore.getState().diagnostics;
    expect(d.rtt_ms).toBe(54);
    expect(d.packet_loss_pct).toBeCloseTo(0.3);
    expect(d.jitter_ms).toBe(0); // unchanged
  });

  it("appends transcript items and keeps last 50", () => {
    for (let i = 0; i < 55; i++) {
      useCallStore.getState().addTranscript({
        direction: "outgoing",
        language: "en",
        text: `msg ${i}`,
        timestamp: Date.now(),
        is_final: true,
      });
    }
    const transcripts = useCallStore.getState().transcripts;
    expect(transcripts).toHaveLength(50);
    const first = transcripts[0];
    const last = transcripts[49];
    expect(first?.text).toBe("msg 5");
    expect(last?.text).toBe("msg 54");
  });
});
