import React, { useEffect, useState, useRef } from "react";

interface AudioVisualizerProps {
  isActive: boolean;
  isSpeaking: boolean;
  statusText?: string;
  latestTranscript?: string;
  localTrack?: MediaStreamTrack | null;
}

export const AudioVisualizer: React.FC<AudioVisualizerProps> = ({
  isActive,
  isSpeaking,
  statusText = "Listening",
  latestTranscript = "",
  localTrack,
}) => {
  const [levels, setLevels] = useState<number[]>([15, 20, 30, 25, 18]);
  const [isLocallySpeaking, setIsLocallySpeaking] = useState(false);
  const animRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);

  // Set up real Web Audio analyser when localTrack is present
  useEffect(() => {
    if (!localTrack || localTrack.readyState !== "live") {
      if (audioCtxRef.current) {
        audioCtxRef.current.close().catch(() => {});
        audioCtxRef.current = null;
        analyserRef.current = null;
      }
      return;
    }

    try {
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      const ctx = new AudioCtx();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 64;
      analyser.smoothingTimeConstant = 0.6;

      const mediaStream = new MediaStream([localTrack]);
      const source = ctx.createMediaStreamSource(mediaStream);
      source.connect(analyser);

      audioCtxRef.current = ctx;
      analyserRef.current = analyser;
    } catch (e) {
      console.warn("[visualizer] web audio setup failed:", e);
    }

    return () => {
      if (audioCtxRef.current) {
        audioCtxRef.current.close().catch(() => {});
        audioCtxRef.current = null;
        analyserRef.current = null;
      }
    };
  }, [localTrack]);

  useEffect(() => {
    if (!isActive) return;

    const dataArray = new Uint8Array(32);

    const updateLevels = () => {
      let localVolume = 0;
      let barLevels = [15, 20, 30, 25, 18];

      if (analyserRef.current) {
        analyserRef.current.getByteFrequencyData(dataArray);
        // Calculate average volume
        let sum = 0;
        for (let i = 0; i < 16; i++) {
          sum += dataArray[i];
        }
        localVolume = sum / 16 / 255; // 0.0 to 1.0

        if (localVolume > 0.04) {
          setIsLocallySpeaking(true);
          barLevels = [
            Math.min(100, Math.max(15, (dataArray[1] / 255) * 110)),
            Math.min(100, Math.max(20, (dataArray[3] / 255) * 130)),
            Math.min(100, Math.max(25, (dataArray[5] / 255) * 150)),
            Math.min(100, Math.max(20, (dataArray[7] / 255) * 130)),
            Math.min(100, Math.max(15, (dataArray[9] / 255) * 110)),
          ];
        } else {
          setIsLocallySpeaking(false);
        }
      }

      if (isSpeaking) {
        // Remote translation is playing
        setLevels([
          Math.random() * 60 + 35,
          Math.random() * 85 + 40,
          Math.random() * 100 + 50,
          Math.random() * 75 + 35,
          Math.random() * 55 + 25,
        ]);
      } else if (localVolume > 0.04) {
        setLevels(barLevels);
      } else {
        // Idle gentle breathing animation
        const t = Date.now() / 400;
        setLevels([
          15 + Math.sin(t) * 8,
          22 + Math.sin(t + 1) * 10,
          28 + Math.sin(t + 2) * 12,
          22 + Math.sin(t + 3) * 10,
          15 + Math.sin(t + 4) * 8,
        ]);
      }

      animRef.current = requestAnimationFrame(updateLevels);
    };

    animRef.current = requestAnimationFrame(updateLevels);

    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, [isActive, isSpeaking]);

  const activeMode = isSpeaking
    ? "remote"
    : isLocallySpeaking
    ? "local"
    : "idle";

  return (
    <div className="flex flex-col items-center gap-4 w-full max-w-sm px-4">
      {/* Visualizer bars */}
      <div className="flex items-center justify-center gap-1.5 h-16 w-full">
        {levels.map((lvl, idx) => (
          <div
            key={idx}
            className={`w-2.5 rounded-full transition-all duration-75 ${
              activeMode === "remote"
                ? "bg-gradient-to-t from-sky-500 to-indigo-400 shadow-lg shadow-sky-500/40"
                : activeMode === "local"
                ? "bg-gradient-to-t from-emerald-500 to-teal-300 shadow-lg shadow-emerald-500/40"
                : "bg-slate-700/80"
            }`}
            style={{ height: `${Math.max(10, Math.min(100, lvl))}%` }}
          />
        ))}
      </div>

      {/* Voice Status Pill */}
      <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-slate-900/90 border border-slate-700 text-xs font-medium shadow-inner">
        <span
          className={`h-2.5 w-2.5 rounded-full ${
            activeMode === "remote"
              ? "bg-sky-400 animate-ping"
              : activeMode === "local"
              ? "bg-emerald-400 animate-pulse"
              : "bg-slate-500"
          }`}
        />
        <span className="text-slate-200">
          {activeMode === "local"
            ? "Microphone: You are speaking..."
            : activeMode === "remote"
            ? "Translation: Playing audio..."
            : statusText}
        </span>
      </div>

      {/* Real-time Subtitles / Live Transcript pill */}
      {latestTranscript && (
        <div className="w-full bg-slate-900/90 border border-sky-500/30 rounded-xl p-3 text-center shadow-lg backdrop-blur animate-fade-in">
          <span className="text-[10px] uppercase tracking-wider text-sky-400 font-semibold block mb-1">
            Live Subtitles / Translation
          </span>
          <p className="text-sm font-medium text-slate-100 leading-relaxed">
            "{latestTranscript}"
          </p>
        </div>
      )}
    </div>
  );
};
