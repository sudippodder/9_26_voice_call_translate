import React from "react";
import { Icon } from "./icons";

interface Props {
  label: string;
  active?: boolean;
  danger?: boolean;
  onClick: () => void;
  icon: keyof typeof Icon;
  disabled?: boolean;
}

export const CallButton: React.FC<Props> = ({ label, active, danger, onClick, icon, disabled }) => {
  const IconCmp = Icon[icon];
  const base =
    "flex flex-col items-center justify-center gap-2 rounded-2xl border transition-all select-none w-20 h-20 sm:w-24 sm:h-24 disabled:opacity-40 disabled:cursor-not-allowed";
  const variant = danger
    ? "bg-rose-600/90 hover:bg-rose-500 border-rose-400 text-white"
    : active
    ? "bg-brand-600 hover:bg-brand-500 border-brand-400 text-white"
    : "bg-slate-800 hover:bg-slate-700 border-slate-700 text-slate-200";
  return (
    <button
      disabled={disabled}
      onClick={onClick}
      className={`${base} ${variant}`}
      aria-label={label}
      title={label}
    >
      <IconCmp className="h-6 w-6" />
      <span className="text-xs font-medium">{label}</span>
    </button>
  );
};
