/** Shared TypeScript types between frontend and backend. */

export interface Language {
  code: string;
  name: string;
  native_name: string;
}

export type CallStatus =
  | "pending"
  | "ringing"
  | "active"
  | "ended"
  | "rejected"
  | "missed";

export interface User {
  id: string;
  email?: string | null;
  display_name?: string | null;
  created_at?: string | null;
}

export interface CallParticipant {
  user_id: string;
  source_language: string;
  target_language: string;
  joined_at?: string | null;
  left_at?: string | null;
}

export interface Call {
  id: string;
  caller_id: string;
  receiver_id: string;
  status: CallStatus;
  started_at?: string | null;
  ended_at?: string | null;
  created_at: string;
  participants: CallParticipant[];
}

export interface CreateCallRequest {
  callee_id: string;
  source_language: string;
  target_language: string;
}

export interface CreateCallResponse {
  call_id: string;
  room_name: string;
  livekit_url: string;
  participant_token: string;
}

export interface AcceptCallResponse {
  call_id: string;
  room_name: string;
  livekit_url: string;
  participant_token: string;
}

/** Frontend call state machine (see Section 19). */
export type CallState =
  | "IDLE"
  | "CALLING"
  | "CONNECTING"
  | "CONNECTED"
  | "ACTIVE"
  | "RECONNECTING"
  | "ENDED";

/** Per-direction translation state. */
export type TranslationState =
  | "IDLE"
  | "LISTENING"
  | "TRANSLATING"
  | "PLAYING"
  | "INTERRUPTED"
  | "ERROR";

export interface TranscriptItem {
  id: string;
  direction: "outgoing" | "incoming";
  language: string;
  text: string;
  timestamp: number;
  is_final: boolean;
}

export interface CallDiagnostics {
  connection_quality: "good" | "fair" | "poor";
  rtt_ms: number;
  packet_loss_pct: number;
  jitter_ms: number;
  ttfa_ms?: number;
  ai_session_connected: boolean;
  buffer_ms: number;
  stall_count: number;
  interrupt_count: number;
  reconnect_count: number;
}
