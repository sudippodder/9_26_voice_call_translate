import React from "react";
import type { Language } from "@/types";

interface Props {
  value: string;
  onChange: (code: string) => void;
  languages: Language[];
  label: string;
}

export const LanguageSelect: React.FC<Props> = ({ value, onChange, languages, label }) => {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</span>
      <div className="relative">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full appearance-none rounded-xl border border-slate-700 bg-slate-900/80 px-4 py-3 pr-10 text-base font-medium text-slate-100 outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30"
        >
          {languages.map((l) => (
            <option key={l.code} value={l.code}>
              {l.native_name} — {l.name}
            </option>
          ))}
        </select>
        <svg
          className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </div>
    </label>
  );
};
