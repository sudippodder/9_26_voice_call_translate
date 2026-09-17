import React, { useEffect, useRef, useState } from "react";
import { useCallStore } from "@/store/callStore";
import { Modal } from "./Modal";
import { Icon } from "./icons";

/**
 * MicTestModal — records audio, plays it back, then sends to /api/v1/test/translate
 * to verify the full translation pipeline.
 *
 * Three-step flow:
 * 1. Record 5 seconds of audio from mic
 * 2. Play back original audio (verify mic works)
 * 3. Send to API for translation → play translated audio (verify OpenAI works)
 */
export const MicTestModal: React.FC<{ open: boolean; onClose: () => void }> = ({
  open,
  onClose,
}) => {
  const authToken = useCallStore((s) => s.authToken);
  const mySpeak = useCallStore((s) => s.mySpeak);
  const myHear = useCallStore((s) => s.myHear);
  const languages = useCallStore((s) => s.languages);

  const [recording, setRecording] = useState(false);
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null);
  const [recordedUrl, setRecordedUrl] = useState<string | null>(null);
  const [translating, setTranslating] = useState(false);
  const [translatedUrl, setTranslatedUrl] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [openaiConfigured, setOpenaiConfigured] = useState<boolean | null>(null);
  const [provider, setProvider] = useState<string>("openai");

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Check provider status on open
  useEffect(() => {
    if (!open) return;
    setOpenaiConfigured(null);
    fetch("/api/v1/test/openai-status")
      .then((r) => r.json())
      .then((d) => {
        setOpenaiConfigured(d.configured);
        setProvider(d.provider || "openai");
      })
      .catch(() => setOpenaiConfigured(false));
  }, [open]);

  // Cleanup on close
  useEffect(() => {
    if (!open) {
      resetAll();
    }
  }, [open]);

  const resetAll = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    setRecording(false);
    setRecordedBlob(null);
    if (recordedUrl) URL.revokeObjectURL(recordedUrl);
    setRecordedUrl(null);
    if (translatedUrl) URL.revokeObjectURL(translatedUrl);
    setTranslatedUrl(null);
    setTranscript("");
    setError(null);
    setSecondsLeft(0);
  };

  const startRecording = async () => {
    setError(null);
    setRecordedBlob(null);
    if (recordedUrl) URL.revokeObjectURL(recordedUrl);
    setRecordedUrl(null);
    if (translatedUrl) URL.revokeObjectURL(translatedUrl);
    setTranslatedUrl(null);
    setTranscript("");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
          sampleRate: 24000,
        },
      });
      streamRef.current = stream;

      // Use webm/opus if available, fallback to audio/wav
      let mimeType = "audio/webm;codecs=opus";
      if (!MediaRecorder.isTypeSupported(mimeType)) {
        mimeType = "audio/webm";
        if (!MediaRecorder.isTypeSupported(mimeType)) {
          mimeType = "audio/ogg;codecs=opus";
          if (!MediaRecorder.isTypeSupported(mimeType)) {
            mimeType = ""; // default
          }
        }
      }

      const mr = new MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined,
      );
      mediaRecorderRef.current = mr;
      chunksRef.current = [];

      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      mr.onstop = () => {
        const blob = new Blob(chunksRef.current, {
          type: mr.mimeType || "audio/webm",
        });
        setRecordedBlob(blob);
        setRecordedUrl(URL.createObjectURL(blob));
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
        }
      };

      mr.start();
      setRecording(true);
      setSecondsLeft(5);

      // 5-second countdown
      timerRef.current = setInterval(() => {
        setSecondsLeft((s) => {
          if (s <= 1) {
            if (timerRef.current) clearInterval(timerRef.current);
            if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
              mediaRecorderRef.current.stop();
            }
            setRecording(false);
            return 0;
          }
          return s - 1;
        });
      }, 1000);
    } catch (e: any) {
      setError(
        e?.message ||
          "Could not access microphone. Check browser permissions.",
      );
    }
  };

  const stopRecordingEarly = () => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
    setRecording(false);
    setSecondsLeft(0);
  };

  const runTranslation = async () => {
    if (!recordedBlob || !authToken) return;
    setTranslating(true);
    setError(null);
    if (translatedUrl) URL.revokeObjectURL(translatedUrl);
    setTranslatedUrl(null);
    setTranscript("");

    const formData = new FormData();
    formData.append("audio", recordedBlob, "recording.webm");
    formData.append("source_language", mySpeak);
    formData.append("target_language", myHear);

    try {
      const resp = await fetch("/api/v1/test/translate", {
        method: "POST",
        headers: { Authorization: `Bearer ${authToken}` },
        body: formData,
      });

      if (!resp.ok) {
        let detail = `Server returned ${resp.status}`;
        try {
          const j = await resp.json();
          detail = j.detail || JSON.stringify(j);
        } catch {}
        throw new Error(detail);
      }

      const blob = await resp.blob();
      setTranslatedUrl(URL.createObjectURL(blob));

      // Read transcript from custom header (decode from latin-1)
      const tHeader = resp.headers.get("X-Transcript");
      if (tHeader) {
        try {
          setTranscript(decodeURIComponent(escape(tHeader)));
        } catch {
          setTranscript(tHeader);
        }
      }
    } catch (e: any) {
      setError(e?.message || "Translation failed");
    } finally {
      setTranslating(false);
    }
  };

  const speakName = languages.find((l) => l.code === mySpeak)?.name || mySpeak;
  const hearName = languages.find((l) => l.code === myHear)?.name || myHear;

  return (
    <Modal open={open} onClose={onClose} title="Mic Test & Translation Test">
      <div className="space-y-4 text-sm">
        {/* Status row */}
        <div className="rounded-lg bg-slate-800/50 p-3 text-xs">
          <div className="flex justify-between">
            <span className="text-slate-400">Translate:</span>
            <span>
              {speakName} → {hearName}
            </span>
          </div>
          <div className="mt-1 flex justify-between">
            <span className="text-slate-400">
              {provider === "gemini" ? "Gemini:" : "OpenAI:"}
            </span>
            {openaiConfigured === null ? (
              <span className="text-slate-500">checking…</span>
            ) : openaiConfigured ? (
              <span className="text-emerald-400">{provider} configured ✓</span>
            ) : (
              <span className="text-rose-400">
                {provider === "gemini"
                  ? "GEMINI_API_KEY missing — get one at https://aistudio.google.com/apikey"
                  : "OPENAI_API_KEY missing — set TRANSLATOR_PROVIDER=gemini to use free Gemini instead"}
              </span>
            )}
          </div>
        </div>

        {/* Step 1: Record */}
        <div className="space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Step 1 — Record your voice
          </div>
          {!recording ? (
            <button
              onClick={startRecording}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 px-4 py-2.5 font-medium text-white hover:bg-brand-500"
            >
              <Icon.Mic className="h-4 w-4" />
              Record 5 seconds
            </button>
          ) : (
            <button
              onClick={stopRecordingEarly}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-rose-600 px-4 py-2.5 font-medium text-white hover:bg-rose-500"
            >
              <Icon.MicOff className="h-4 w-4" />
              Stop ({secondsLeft}s)
            </button>
          )}
          {recording && (
            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-800">
              <div
                className="h-full bg-brand-500 transition-all"
                style={{ width: `${((5 - secondsLeft) / 5) * 100}%` }}
              />
            </div>
          )}
        </div>

        {/* Step 2: Play original */}
        {recordedUrl && (
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Step 2 — Verify your mic
            </div>
            <audio src={recordedUrl} controls className="w-full" />
          </div>
        )}

        {/* Step 3: Translate */}
        {recordedBlob && (
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Step 3 — Test translation
            </div>
            <button
              onClick={runTranslation}
              disabled={translating || !openaiConfigured}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 py-2.5 font-medium text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {translating ? (
                <>
                  <svg
                    className="h-4 w-4 animate-spin"
                    viewBox="0 0 24 24"
                    fill="none"
                  >
                    <circle
                      className="opacity-25"
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeWidth="4"
                    />
                    <path
                      className="opacity-90"
                      fill="currentColor"
                      d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4z"
                    />
                  </svg>
                  Translating…
                </>
              ) : (
                <>
                  <Icon.Globe className="h-4 w-4" />
                  Translate {speakName} → {hearName}
                </>
              )}
            </button>
            {!openaiConfigured && (
              <p className="text-xs text-rose-400">
                {provider === "gemini"
                  ? "Gemini is not configured. Get a free key at https://aistudio.google.com/apikey and set GEMINI_API_KEY in .env"
                  : "OpenAI is not configured. Either set OPENAI_API_KEY in .env, OR switch to free Gemini by setting TRANSLATOR_PROVIDER=gemini and GEMINI_API_KEY=... in .env"}
              </p>
            )}
          </div>
        )}

        {/* Step 4: Play translation */}
        {translatedUrl && (
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-emerald-400">
              ✓ Translation complete — play it back
            </div>
            <audio src={translatedUrl} controls autoPlay className="w-full" />
            {transcript && (
              <div className="rounded-lg bg-slate-800/50 p-2 text-xs">
                <div className="text-slate-400">Transcript:</div>
                <div className="mt-0.5 text-slate-200">{transcript}</div>
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="rounded-lg bg-rose-900/30 p-2 text-xs text-rose-300">
            {error}
          </div>
        )}

        <div className="border-t border-slate-800 pt-3 text-[11px] text-slate-500">
          This tool records audio, sends it to the OpenAI Realtime API, and plays
          the translated audio back. It's a one-shot test — not the live call
          path. Useful for verifying your mic and OpenAI account both work
          before making a real call.
        </div>
      </div>
    </Modal>
  );
};
