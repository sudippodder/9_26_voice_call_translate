import React from "react";
import { useCallStore } from "@/store/callStore";

export const DiagnosticsPanel: React.FC = () => {
  const { diagnostics, showDiagnostics } = useCallStore();
  if (!showDiagnostics) return null;

  const q = diagnostics.connection_quality;
  const qualityColor =
    q === "good" ? "text-emerald-400" : q === "fair" ? "text-amber-400" : "text-rose-400";

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900/80 p-4 text-xs font-mono backdrop-blur">
      <div className="mb-2 flex items-center gap-2 text-slate-200">
        <IconActivity className="h-4 w-4 text-brand-400" />
        <span className="font-semibold uppercase tracking-wide">Diagnostics</span>
      </div>
      <div className="grid grid-cols-2 gap-y-1.5">
        <div className="text-slate-400">Connection</div>
        <div className={qualityColor}>{q}</div>
        <div className="text-slate-400">RTT</div>
        <div>{diagnostics.rtt_ms} ms</div>
        <div className="text-slate-400">Packet loss</div>
        <div>{diagnostics.packet_loss_pct.toFixed(2)}%</div>
        <div className="text-slate-400">Jitter</div>
        <div>{diagnostics.jitter_ms} ms</div>
        <div className="text-slate-400 border-t border-slate-800 pt-1.5 mt-1.5">TTFA</div>
        <div className="border-t border-slate-800 pt-1.5 mt-1.5">
          {diagnostics.ttfa_ms ? `${diagnostics.ttfa_ms.toFixed(0)} ms` : "—"}
        </div>
        <div className="text-slate-400">AI session</div>
        <div>{diagnostics.ai_session_connected ? "Connected" : "—"}</div>
        <div className="text-slate-400">Buffer</div>
        <div>{diagnostics.buffer_ms} ms</div>
        <div className="text-slate-400">Stalls</div>
        <div>{diagnostics.stall_count}</div>
        <div className="text-slate-400">Interrupts</div>
        <div>{diagnostics.interrupt_count}</div>
        <div className="text-slate-400">Reconnects</div>
        <div>{diagnostics.reconnect_count}</div>
      </div>
    </div>
  );
};

const IconActivity = ({ className }: { className?: string }) => (
  <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
  </svg>
);
