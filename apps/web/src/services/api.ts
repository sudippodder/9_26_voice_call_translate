import type { Language } from "@/types";

const API_URL = (import.meta.env.VITE_API_URL as string) || "";

export interface DevLoginResponse {
  access_token: string;
  token_type: string;
  user: { id: string; email?: string | null; display_name?: string | null };
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

export interface UserSearchResult {
  id: string;
  email?: string | null;
  display_name?: string | null;
}

export interface IncomingCall {
  call_id: string;
  caller_id: string;
  caller_name: string;
  caller_email?: string | null;
  caller_speak?: string;
  caller_hear?: string;
  status: string;
  created_at?: string | null;
}

function authHeaders(token: string): Record<string, string> {
  return { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
}

async function handle<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const j = await resp.json();
      detail = j.detail || JSON.stringify(j);
    } catch {
      /* ignore */
    }
    throw new Error(`API ${resp.status}: ${detail}`);
  }
  if (resp.status === 204) return undefined as unknown as T;
  return (await resp.json()) as T;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
export async function devLogin(email: string, displayName?: string): Promise<DevLoginResponse> {
  const r = await fetch(`${API_URL}/api/v1/auth/dev-login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, display_name: displayName }),
  });
  return handle<DevLoginResponse>(r);
}

export async function fetchMe(token: string): Promise<{ id: string; email?: string | null; display_name?: string | null }> {
  const r = await fetch(`${API_URL}/api/v1/me`, { headers: authHeaders(token) });
  return handle(r);
}

// ---------------------------------------------------------------------------
// Languages
// ---------------------------------------------------------------------------
export async function fetchLanguages(): Promise<Language[]> {
  const r = await fetch(`${API_URL}/api/v1/languages`);
  const j = await handle<{ languages: Language[] }>(r);
  return j.languages;
}

// ---------------------------------------------------------------------------
// Users — search by email / display name
// ---------------------------------------------------------------------------
export async function searchUsers(token: string, query: string): Promise<UserSearchResult[]> {
  if (query.trim().length < 2) return [];
  const r = await fetch(
    `${API_URL}/api/v1/users/search?q=${encodeURIComponent(query)}`,
    { headers: authHeaders(token) },
  );
  return handle<UserSearchResult[]>(r);
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------
export async function createCall(
  token: string,
  payload: CreateCallRequest,
): Promise<CreateCallResponse> {
  const r = await fetch(`${API_URL}/api/v1/calls`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(payload),
  });
  return handle<CreateCallResponse>(r);
}

export async function acceptCall(
  token: string,
  callId: string,
): Promise<{ call_id: string; room_name: string; livekit_url: string; participant_token: string }> {
  const r = await fetch(`${API_URL}/api/v1/calls/${callId}/accept`, {
    method: "POST",
    headers: authHeaders(token),
  });
  return handle(r);
}

export async function rejectCall(token: string, callId: string): Promise<void> {
  const r = await fetch(`${API_URL}/api/v1/calls/${callId}/reject`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (!r.ok && r.status !== 204) {
    throw new Error(`reject_call failed: ${r.status}`);
  }
}

export async function endCall(token: string, callId: string): Promise<void> {
  const r = await fetch(`${API_URL}/api/v1/calls/${callId}/end`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (!r.ok && r.status !== 204) {
    throw new Error(`end_call failed: ${r.status}`);
  }
}

export async function getCallStatus(token: string, callId: string): Promise<any> {
  const r = await fetch(`${API_URL}/api/v1/calls/${callId}`, { headers: authHeaders(token) });
  return handle(r);
}

export async function listIncomingCalls(token: string): Promise<IncomingCall[]> {
  const r = await fetch(`${API_URL}/api/v1/calls/incoming`, { headers: authHeaders(token) });
  return handle<IncomingCall[]>(r);
}

export async function listCalls(token: string) {
  const r = await fetch(`${API_URL}/api/v1/calls`, { headers: authHeaders(token) });
  return handle<{ calls: any[] }>(r);
}

export async function fetchHealth(): Promise<{ status: string; version: string; app_env: string }> {
  const r = await fetch(`${API_URL}/health`);
  return handle(r);
}

