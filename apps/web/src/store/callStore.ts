import { create } from "zustand";
import type {
  CallState,
  TranslationState,
  Language,
  TranscriptItem,
  CallDiagnostics,
  User,
} from "@/types";
import type { UserSearchResult, IncomingCall } from "@/services/api";

// ---------------------------------------------------------------------------
// Store shape
// ---------------------------------------------------------------------------
interface CallStore {
  // Auth
  authToken: string | null;
  user: User | null;

  // Languages
  languages: Language[];
  mySpeak: string;   // ISO code user speaks
  myHear: string;    // ISO code user wants to hear

  // User search
  searchQuery: string;
  searchResults: UserSearchResult[];
  searchBusy: boolean;
  selectedCallee: UserSearchResult | null;

  // Call state
  callState: CallState;
  translationState: TranslationState;
  callId: string | null;
  roomName: string | null;
  livekitUrl: string | null;
  participantToken: string | null;
  partnerId: string | null;
  partnerName: string | null;
  micMuted: boolean;
  speakerMuted: boolean;
  showDiagnostics: boolean;
  showTranscript: boolean;

  // Incoming calls (receiver side)
  incomingCalls: IncomingCall[];
  activeIncomingCall: IncomingCall | null;

  // Diagnostics
  diagnostics: CallDiagnostics;

  // Transcripts (optional captions)
  transcripts: TranscriptItem[];

  // Actions
  setAuth: (token: string, user: User) => void;
  logout: () => void;
  setLanguages: (langs: Language[]) => void;
  setSpeak: (code: string) => void;
  setHear: (code: string) => void;
  setSearchQuery: (q: string) => void;
  setSearchResults: (r: UserSearchResult[]) => void;
  setSearchBusy: (b: boolean) => void;
  selectCallee: (u: UserSearchResult | null) => void;
  setCallState: (s: CallState) => void;
  setTranslationState: (s: TranslationState) => void;
  setCallInfo: (info: {
    callId: string;
    roomName: string;
    livekitUrl: string;
    participantToken: string;
    partnerId: string;
    partnerName?: string;
    callState?: CallState;
  }) => void;
  setIncomingCalls: (calls: IncomingCall[]) => void;
  setActiveIncomingCall: (c: IncomingCall | null) => void;
  toggleMute: () => void;
  toggleSpeaker: () => void;
  toggleDiagnostics: () => void;
  toggleTranscript: () => void;
  resetCall: () => void;
  addTranscript: (item: Omit<TranscriptItem, "id">) => void;
  updateDiagnostics: (d: Partial<CallDiagnostics>) => void;
}

const initialCallDiagnostics: CallDiagnostics = {
  connection_quality: "good",
  rtt_ms: 0,
  packet_loss_pct: 0,
  jitter_ms: 0,
  ttfa_ms: undefined,
  ai_session_connected: false,
  buffer_ms: 100,
  stall_count: 0,
  interrupt_count: 0,
  reconnect_count: 0,
};

let transcriptSeq = 0;
function nextTranscriptId(): string {
  transcriptSeq += 1;
  return `t-${transcriptSeq}`;
}

export const useCallStore = create<CallStore>((set) => ({
  authToken: null,
  user: null,

  languages: [],
  mySpeak: "en",
  myHear: "hi",

  searchQuery: "",
  searchResults: [],
  searchBusy: false,
  selectedCallee: null,

  callState: "IDLE",
  translationState: "IDLE",
  callId: null,
  roomName: null,
  livekitUrl: null,
  participantToken: null,
  partnerId: null,
  partnerName: null,
  micMuted: false,
  speakerMuted: false,
  showDiagnostics: false,
  showTranscript: false,

  incomingCalls: [],
  activeIncomingCall: null,

  diagnostics: initialCallDiagnostics,
  transcripts: [],

  setAuth: (token, user) => set({ authToken: token, user }),
  logout: () => set({ authToken: null, user: null, callId: null, callState: "IDLE" }),

  setLanguages: (langs) => set({ languages: langs }),
  setSpeak: (code) => set({ mySpeak: code }),
  setHear: (code) => set({ myHear: code }),

  setSearchQuery: (q) => set({ searchQuery: q }),
  setSearchResults: (r) => set({ searchResults: r }),
  setSearchBusy: (b) => set({ searchBusy: b }),
  selectCallee: (u) => set({ selectedCallee: u }),

  setCallState: (s) => set({ callState: s }),
  setTranslationState: (s) => set({ translationState: s }),

  setCallInfo: (info) =>
    set((s) => ({
      callId: info.callId,
      roomName: info.roomName,
      livekitUrl: info.livekitUrl,
      participantToken: info.participantToken,
      partnerId: info.partnerId,
      partnerName: info.partnerName ?? info.partnerId,
      callState: info.callState || (s.callState === "CALLING" ? "CALLING" : "CONNECTING"),
    })),

  setIncomingCalls: (calls) => set({ incomingCalls: calls }),
  setActiveIncomingCall: (c) => set({ activeIncomingCall: c }),

  toggleMute: () => set((s) => ({ micMuted: !s.micMuted })),
  toggleSpeaker: () => set((s) => ({ speakerMuted: !s.speakerMuted })),
  toggleDiagnostics: () => set((s) => ({ showDiagnostics: !s.showDiagnostics })),
  toggleTranscript: () => set((s) => ({ showTranscript: !s.showTranscript })),

  resetCall: () =>
    set({
      callState: "IDLE",
      translationState: "IDLE",
      callId: null,
      roomName: null,
      livekitUrl: null,
      participantToken: null,
      partnerId: null,
      partnerName: null,
      micMuted: false,
      speakerMuted: false,
      transcripts: [],
      diagnostics: initialCallDiagnostics,
      activeIncomingCall: null,
    }),

  addTranscript: (item) =>
    set((s) => ({
      transcripts: [
        ...s.transcripts.slice(-49), // keep last 49, then append → 50 total
        { ...item, id: nextTranscriptId() },
      ],
    })),

  updateDiagnostics: (d) => set((s) => ({ diagnostics: { ...s.diagnostics, ...d } })),
}));
