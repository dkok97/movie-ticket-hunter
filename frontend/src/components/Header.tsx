import type { BotStatus } from "../types";

interface Props {
  status: BotStatus | null;
}

export default function Header({ status }: Props) {
  return (
    <header className="border-b border-cinema-600/30 bg-cinema-800/40 backdrop-blur-md sticky top-0 z-50">
      <div className="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
        {/* Left: Brand */}
        <div className="flex items-center gap-3">
          <div className="text-3xl">🎬</div>
          <div>
            <h1 className="text-xl font-bold tracking-tight">
              <span className="text-gold-500">Odyssey</span>{" "}
              <span className="text-slate-200">IMAX 70mm Hunter</span>
            </h1>
            {status && (
              <p className="text-xs text-slate-500 mt-0.5">
                Tracking: <span className="text-slate-400">{status.movie_name}</span>
                {status.movie_format && (
                  <span className="text-gold-400"> · {status.movie_format}</span>
                )}
                {status.start_date && status.end_date && (
                  <>
                    {" · "}
                    <span className="text-slate-400">
                      {status.start_date} → {status.end_date}
                    </span>
                    {status.dates_tracked > 0 && (
                      <span className="text-slate-500"> ({status.dates_tracked} days)</span>
                    )}
                  </>
                )}
              </p>
            )}
          </div>
        </div>

        {/* Right: Status indicator */}
        <div className="flex items-center gap-3">
          {status ? (
            <div className="flex items-center gap-2 bg-cinema-700/60 px-4 py-2 rounded-xl border border-cinema-600/40">
              <div
                className={
                  status.is_checking
                    ? "status-dot-active"
                    : status.scheduler_running
                      ? "status-dot bg-emerald-400"
                      : "status-dot-idle"
                }
              />
              <span className="text-sm font-medium text-slate-300">
                {status.is_checking
                  ? "Checking..."
                  : status.scheduler_running
                    ? "Active"
                    : "Idle"}
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2 bg-cinema-700/60 px-4 py-2 rounded-xl border border-cinema-600/40">
              <div className="status-dot-idle animate-pulse" />
              <span className="text-sm text-slate-500">Connecting...</span>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
