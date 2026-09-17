import React, { useState } from "react";
import { playTestSound } from "@/utils/audioTest";
import { Icon } from "@/components/icons";

interface SoundTestButtonProps {
  className?: string;
  variant?: "primary" | "secondary" | "minimal";
  size?: "sm" | "md";
}

export const SoundTestButton: React.FC<SoundTestButtonProps> = ({
  className = "",
  variant = "secondary",
  size = "md",
}) => {
  const [playing, setPlaying] = useState(false);

  const handleTest = async () => {
    if (playing) return;
    setPlaying(true);
    try {
      await playTestSound();
    } catch (err) {
      console.error("Audio test error:", err);
    } finally {
      setTimeout(() => setPlaying(false), 2000);
    }
  };

  const baseStyles =
    "inline-flex items-center gap-2 font-medium rounded-lg transition-all focus:outline-none focus:ring-2 focus:ring-sky-400/50";
  
  const sizeStyles =
    size === "sm"
      ? "px-3 py-1.5 text-xs"
      : "px-4 py-2 text-sm";

  const variantStyles = {
    primary: "bg-sky-500 hover:bg-sky-400 text-white shadow-lg shadow-sky-500/20 active:scale-95",
    secondary:
      "bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700/60 active:scale-95",
    minimal:
      "text-slate-400 hover:text-sky-400 hover:bg-slate-800/60 active:scale-95",
  }[variant];

  return (
    <button
      type="button"
      onClick={handleTest}
      disabled={playing}
      title="Test Speaker & Sound Output"
      className={`${baseStyles} ${sizeStyles} ${variantStyles} ${className} ${
        playing ? "animate-pulse ring-2 ring-sky-400" : ""
      }`}
    >
      <Icon.Volume
        className={`h-4 w-4 ${playing ? "text-sky-400 animate-bounce" : "text-slate-400"}`}
      />
      <span>{playing ? "Playing Test Sound..." : "Test Sound"}</span>
    </button>
  );
};
