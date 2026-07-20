import type { BotStatus } from "../types";
import { api } from "../api";
import { useState } from "react";

interface Props {
  status: BotStatus | null;
  triggering: boolean;
  onTrigger: () => void;
  onRefresh: () => void;
}

function timeAgo(iso: string | null): string {
  if (!iso) return "Never";
  const diff = Date.now() - new Date(iso).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function timeUntil(iso: string | null): string {
  if (!iso) return "—";
  const diff = new Date(iso).getTime() - Date.now();
  if (diff < 0) return "Now";
  const secs = Math.floor(diff / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  return `${Math.floor(mins / 60)}h ${mins % 60}m`;
}

export default function Dashboard({ status, triggering, onTrigger, onRefresh }: Props) {
  const [testingSent, setTestingSent] = useState(false);

  const handleTestNotification = async () => {
    try {
      await api.testNotification();
      setTestingSent(true);
      setTimeout(() => setTestingSent(false), 3000);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="space-y-6">
      {/* Stats cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          emoji="🔄"
          label="Total Checks"
          value={status?.total_runs?.toString() ?? "—"}
          sub="bot runs completed"
        />
        <StatCard
          emoji="🎟️"
          label="Tickets Found"
          value={status?.total_findings?.toString() ?? "0"}
          sub={status?.total_findings ? "results detected!" : "none yet"}
          highlight={!!status?.total_findings}
        />
        <StatCard
          emoji="🕐"
          label="Last Check"
          value={timeAgo(status?.last_run?.started_at ?? null)}
          sub={
            status?.last_run
              ? status.last_run.status === "completed"
                ? "✅ Completed"
                : status.last_run.status === "error"
                  ? "❌ Error"
                  : "⏳ Running"
              : "No runs yet"
          }
        />
        <StatCard
          emoji="⏭️"
          label="Next Check"
          value={timeUntil(status?.next_check ?? null)}
          sub={`Every ${status?.poll_interval_minutes ?? "?"}m`}
        />
      </div>

      {/* Actions row */}
      <div className="card flex flex-wrap items-center gap-4">
        <button
          onClick={onTrigger}
          disabled={triggering || status?.is_checking}
          className="btn-primary"
        >
          {triggering || status?.is_checking ? (
            <>
              <Spinner /> Checking...
            </>
          ) : (
            <>🔍 Run Check Now</>
          )}
        </button>

        <button onClick={onRefresh} className="btn-secondary">
          🔄 Refresh
        </button>

        <button
          onClick={handleTestNotification}
          disabled={testingSent}
          className="btn-secondary"
        >
          {testingSent ? "✅ Sent!" : "🔔 Test Notification"}
        </button>

        {status && (
          <div className="ml-auto text-right">
            <p className="text-xs text-slate-500">
              ntfy topic:{" "}
              <code className="text-gold-500 font-mono bg-cinema-800 px-2 py-0.5 rounded">
                {/* @ts-expect-error status might have ntfy info */}
                {status.ntfy_topic ?? "odyssey-imax-70mm"}
              </code>
            </p>
            <p className="text-xs text-slate-600 mt-1">
              Subscribe at ntfy.sh to get alerts
            </p>
          </div>
        )}
      </div>

      {/* Ticket alert banner */}
      {status && status.total_findings > 0 && (
        <div className="bg-gradient-to-r from-gold-500/20 via-amber-500/15 to-gold-500/20 border border-gold-500/40 rounded-2xl p-6 animate-fade-in">
          <div className="flex items-center gap-4">
            <span className="text-4xl">🎉</span>
            <div>
              <h3 className="text-lg font-bold text-gold-400">
                Ticket Results Detected!
              </h3>
              <p className="text-sm text-amber-200/80 mt-1">
                The bot has found {status.total_findings} result(s). Check the findings
                below for details and booking links.
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({
  emoji,
  label,
  value,
  sub,
  highlight = false,
}: {
  emoji: string;
  label: string;
  value: string;
  sub: string;
  highlight?: boolean;
}) {
  return (
    <div
      className={`card-hover ${
        highlight ? "border-gold-500/40 bg-gold-500/5" : ""
      }`}
    >
      <div className="flex items-start justify-between">
        <span className="text-2xl">{emoji}</span>
        {highlight && <span className="badge-warning">NEW</span>}
      </div>
      <p className="text-sm text-slate-400 mt-3">{label}</p>
      <p
        className={`text-2xl font-bold mt-1 ${
          highlight ? "text-gold-400" : "text-slate-100"
        }`}
      >
        {value}
      </p>
      <p className="text-xs text-slate-500 mt-1">{sub}</p>
    </div>
  );
}

function Spinner() {
  return (
    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}
