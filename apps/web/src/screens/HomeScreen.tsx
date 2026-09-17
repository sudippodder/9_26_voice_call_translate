import React, { useEffect, useRef, useState } from "react";
import { useCallStore } from "@/store/callStore";
import {
  devLogin,
  fetchLanguages,
  fetchMe,
  fetchHealth,
  searchUsers,
} from "@/services/api";
import { LanguageSelect } from "@/components/LanguageSelect";
import { SoundTestButton } from "@/components/SoundTestButton";
import { MicTestModal } from "@/components/MicTestModal";
import { Modal } from "@/components/Modal";
import { Icon } from "@/components/icons";

export const HomeScreen: React.FC = () => {
  const {
    authToken,
    user,
    languages,
    mySpeak,
    myHear,
    setLanguages,
    setSpeak,
    setHear,
    setAuth,
    setCallState,
    selectCallee,
    setSearchQuery,
    setSearchResults,
    setSearchBusy,
    searchQuery,
    searchResults,
    searchBusy,
    selectedCallee,
  } = useCallStore();

  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [loginEmail, setLoginEmail] = useState("");
  const [loginName, setLoginName] = useState("");
  const [loginOpen, setLoginOpen] = useState(false);
  const [apiHealth, setApiHealth] = useState<string>("checking");
  const [micTestOpen, setMicTestOpen] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Auto-open login if no auth
  useEffect(() => {
    if (!authToken) setLoginOpen(true);
  }, [authToken]);

  // Restore session if token present
  useEffect(() => {
    const t = localStorage.getItem("vt_token");
    if (t) {
      fetchMe(t)
        .then((u) => {
          setAuth(t, u as any);
          setLoginOpen(false);
        })
        .catch(() => {
          localStorage.removeItem("vt_token");
          setLoginOpen(true);
        });
    }
  }, [setAuth]);

  // Health check + languages
  useEffect(() => {
    fetchHealth()
      .then((h) => setApiHealth(`${h.status} (${h.app_env})`))
      .catch(() => setApiHealth("unreachable"));
    fetchLanguages().then(setLanguages).catch(() => {});
  }, [setLanguages]);

  // Debounced user search
  useEffect(() => {
    if (!authToken || searchQuery.trim().length < 2) {
      setSearchResults([]);
      return;
    }
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(async () => {
      setSearchBusy(true);
      try {
        const results = await searchUsers(authToken, searchQuery);
        setSearchResults(results);
      } catch (e: any) {
        setErr(e?.message || "Search failed");
        setSearchResults([]);
      } finally {
        setSearchBusy(false);
      }
    }, 250);
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current);
    };
  }, [authToken, searchQuery, setSearchBusy, setSearchResults, setSearchQuery]);

  const handleLogin = async () => {
    setBusy(true);
    setErr(null);
    try {
      const res = await devLogin(loginEmail || "dev@local", loginName || undefined);
      localStorage.setItem("vt_token", res.access_token);
      setAuth(res.access_token, res.user as any);
      setLoginOpen(false);
    } catch (e: any) {
      setErr(e?.message || "Login failed");
    } finally {
      setBusy(false);
    }
  };

  const handleStartCall = (callee: typeof selectedCallee) => {
    if (!callee) return;
    selectCallee(callee);
    setCallState("CALLING");
  };

  return (
    <div className="flex min-h-screen flex-col items-center justify-center p-6">
      <div className="w-full max-w-md space-y-8">
        <header className="text-center">
          <div className="mb-3 inline-flex h-16 w-16 items-center justify-center rounded-2xl bg-brand-600/20 ring-1 ring-brand-500/40">
            <Icon.Globe className="h-9 w-9 text-brand-400" />
          </div>
          <h1 className="text-3xl font-semibold tracking-tight">Voice Translator</h1>
          <p className="mt-2 text-sm text-slate-400">
            Speak naturally. Hear each other in your own language.
          </p>
          <p className="mt-2 text-[11px] uppercase tracking-widest text-slate-500">
            API:{" "}
            <span className={apiHealth.startsWith("ok") ? "text-emerald-400" : "text-amber-400"}>
              {apiHealth}
            </span>
          </p>
        </header>

        {/* My languages */}
        <section className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/60 p-6 backdrop-blur">
          <LanguageSelect label="I speak" value={mySpeak} onChange={setSpeak} languages={languages} />
          <div className="flex items-center justify-center py-1">
            <svg
              className="h-5 w-5 text-slate-600"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="7 10 12 15 17 10" />
            </svg>
          </div>
          <LanguageSelect label="I want to hear" value={myHear} onChange={setHear} languages={languages} />
          <div className="pt-2 flex items-center justify-between border-t border-slate-800/80">
            <span className="text-xs text-slate-400">Sound & Mic Check:</span>
            <div className="flex gap-2">
              <button
                onClick={() => setMicTestOpen(true)}
                className="flex items-center gap-1.5 rounded-lg bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-700"
                title="Record your voice and hear the translation"
              >
                <Icon.Mic className="h-3.5 w-3.5" />
                Mic & Translation Test
              </button>
              <SoundTestButton size="sm" variant="secondary" />
            </div>
          </div>
        </section>

        {/* User search */}
        {authToken && (
          <section className="space-y-3 rounded-2xl border border-slate-800 bg-slate-900/60 p-6 backdrop-blur">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-300">
              Call someone
            </h2>
            <div className="relative">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search by email or name…"
                className="w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 pl-10 text-base outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30"
                disabled={!authToken}
              />
              <svg
                className="pointer-events-none absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-500"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              {searchBusy && (
                <div className="absolute right-3 top-1/2 -translate-y-1/2">
                  <svg className="h-4 w-4 animate-spin text-brand-500" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4z" />
                  </svg>
                </div>
              )}
            </div>

            {/* Search results */}
            {searchResults.length > 0 && (
              <ul className="divide-y divide-slate-800 overflow-hidden rounded-xl border border-slate-800">
                {searchResults.map((u) => (
                  <li
                    key={u.id}
                    className="flex items-center justify-between gap-3 bg-slate-950/60 px-4 py-3 hover:bg-slate-900"
                  >
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">
                        {u.display_name || u.email || u.id.slice(0, 12)}
                      </div>
                      {u.email && u.email !== u.display_name && (
                        <div className="truncate text-xs text-slate-500">{u.email}</div>
                      )}
                    </div>
                    <button
                      onClick={() => handleStartCall(u)}
                      disabled={mySpeak === myHear}
                      className="flex shrink-0 items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-500 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <Icon.Phone className="h-3.5 w-3.5" />
                      Call
                    </button>
                  </li>
                ))}
              </ul>
            )}

            {searchQuery.trim().length >= 2 && !searchBusy && searchResults.length === 0 && (
              <p className="text-center text-xs text-slate-500">No users found.</p>
            )}

            {selectedCallee && (
              <div className="rounded-lg bg-slate-800/60 px-3 py-2 text-xs text-slate-300">
                Selected: <span className="font-medium text-slate-100">{selectedCallee.display_name || selectedCallee.email}</span>
              </div>
            )}

            {err && <p className="text-xs text-rose-400">{err}</p>}
          </section>
        )}

        {/* Selected callee call-out */}
        {authToken && selectedCallee && (
          <button
            onClick={() => handleStartCall(selectedCallee)}
            disabled={!authToken || mySpeak === myHear}
            className="flex w-full items-center justify-center gap-3 rounded-xl bg-brand-600 px-6 py-3.5 text-base font-semibold text-white shadow-lg shadow-brand-600/30 transition hover:bg-brand-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Icon.Phone className="h-5 w-5" />
            Call {selectedCallee.display_name || selectedCallee.email}
          </button>
        )}

        {authToken && user && !selectedCallee && (
          <p className="text-center text-xs text-slate-500">
            Signed in as {user.display_name || user.email || user.id.slice(0, 8)} — search for someone to call above.
          </p>
        )}
      </div>

      <Modal open={loginOpen} onClose={() => {}} title="Sign in (dev mode)">
        <p className="text-slate-400">
          Enter your email to sign in. If two different people each sign in with a different email, they can find each other via the search box. Use Supabase Auth in production.
        </p>
        <input
          type="email"
          value={loginEmail}
          onChange={(e) => setLoginEmail(e.target.value)}
          placeholder="you@example.com"
          className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-brand-500"
        />
        <input
          type="text"
          value={loginName}
          onChange={(e) => setLoginName(e.target.value)}
          placeholder="Display name (optional)"
          className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-brand-500"
        />
        {err && <p className="text-rose-400 text-xs">{err}</p>}
        <button
          disabled={busy}
          onClick={handleLogin}
          className="w-full rounded-lg bg-brand-600 px-4 py-2.5 font-medium text-white hover:bg-brand-500 disabled:opacity-40"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </Modal>

      <MicTestModal open={micTestOpen} onClose={() => setMicTestOpen(false)} />
    </div>
  );
};
