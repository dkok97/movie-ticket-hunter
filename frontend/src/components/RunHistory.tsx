import type { BotRun } from "../types";
import { useState } from "react";
import { api } from "../api";

interface Props {
  runs: BotRun[];
  expanded?: boolean;
  onClear?: () => void;
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function statusIcon(status: string): string {
  switch (status) {
    case "completed":
      return "✅";
    case "error":
      return "❌";
    case "running":
      return "⏳";
    default:
      return "❓";
  }
}

export default function RunHistory({ runs, expanded = false, onClear }: Props) {
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [clearing, setClearing] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const handleClear = async () => {
    if (!confirmClear) {
      setConfirmClear(true);
      setTimeout(() => setConfirmClear(false), 3000);
      return;
    }
    setClearing(true);
    try {
      await api.clearHistory();
      onClear?.();
    } finally {
      setClearing(false);
      setConfirmClear(false);
    }
  };

  return (
    <div className="card">
      <div className="flex items-center gap-3 mb-4">
        <span className="text-xl">📋</span>
        <h2 className="text-lg font-semibold text-slate-200">
          {expanded ? "Full Run History" : "Recent Runs"}
        </h2>
        <span className="badge-info">{runs.length}</span>
        {runs.length > 0 && (
          <button
            onClick={handleClear}
            disabled={clearing}
            className="ml-auto text-xs font-medium px-3 py-1.5 rounded-lg transition-all duration-200 border active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed bg-red-500/10 border-red-500/30 text-red-400 hover:bg-red-500/20 hover:border-red-500/50"
          >
            {clearing ? "Clearing..." : confirmClear ? "Click again to confirm" : "🗑 Clear History"}
          </button>
        )}
      </div>

      {runs.length === 0 ? (
        <div className="text-center py-8">
          <p className="text-slate-400">No runs yet. Click "Run Check Now" to start!</p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-500 text-xs uppercase tracking-wider border-b border-cinema-600/40">
                <th className="pb-3 pr-4">#</th>
                <th className="pb-3 pr-4">Status</th>
                <th className="pb-3 pr-4">When</th>
                <th className="pb-3 pr-4">Duration</th>
                <th className="pb-3 pr-4">Sources</th>
                <th className="pb-3 pr-4">Found</th>
                <th className="pb-3">Details</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-cinema-600/20">
              {runs.map((run) => {
                const duration =
                  run.completed_at && run.started_at
                    ? Math.round(
                        (new Date(run.completed_at).getTime() -
                          new Date(run.started_at).getTime()) /
                          1000,
                      )
                    : null;

                let sources: string[] = [];
                try {
                  sources = run.sources_checked
                    ? JSON.parse(run.sources_checked)
                    : [];
                } catch {
                  sources = run.sources_checked ? [run.sources_checked] : [];
                }

                const isExpanded = expandedId === run.id;

                return (
                  <tr
                    key={run.id}
                    className="hover:bg-cinema-700/30 cursor-pointer transition-colors"
                    onClick={() => setExpandedId(isExpanded ? null : run.id)}
                  >
                    <td className="py-3 pr-4 text-slate-500 font-mono text-xs">
                      {run.id}
                    </td>
                    <td className="py-3 pr-4">
                      <span className="flex items-center gap-2">
                        {statusIcon(run.status)}
                        <span
                          className={
                            run.status === "completed"
                              ? "text-emerald-400"
                              : run.status === "error"
                                ? "text-red-400"
                                : "text-amber-400"
                          }
                        >
                          {run.status}
                        </span>
                      </span>
                    </td>
                    <td className="py-3 pr-4 text-slate-400">
                      {timeAgo(run.started_at)}
                    </td>
                    <td className="py-3 pr-4 text-slate-500 font-mono text-xs">
                      {duration !== null ? `${duration}s` : "—"}
                    </td>
                    <td className="py-3 pr-4">
                      <div className="flex flex-wrap gap-1">
                        {sources.slice(0, 3).map((s, i) => (
                          <span key={i} className="badge-info text-[10px]">
                            {s.split(":")[0]}
                          </span>
                        ))}
                        {sources.length > 3 && (
                          <span className="badge-info text-[10px]">
                            +{sources.length - 3}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="py-3 pr-4">
                      <span
                        className={`font-semibold ${
                          run.tickets_found > 0 ? "text-gold-400" : "text-slate-500"
                        }`}
                      >
                        {run.tickets_found}
                      </span>
                    </td>
                    <td className="py-3">
                      {isExpanded ? (
                        <pre className="text-xs text-slate-400 whitespace-pre-wrap max-w-md font-mono bg-cinema-800/60 p-2 rounded-lg">
                          {run.details || run.error || "No details"}
                        </pre>
                      ) : (
                        <span className="text-slate-600 text-xs">
                          {run.details
                            ? run.details.substring(0, 60) + "..."
                            : run.error
                              ? `Error: ${run.error.substring(0, 40)}...`
                              : "—"}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
