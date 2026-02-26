import type { TicketFinding } from "../types";

interface Props {
  findings: TicketFinding[];
}

function sourceLabel(source: string): string {
  if (source.startsWith("amc")) return "AMC";
  if (source.startsWith("fandango")) return "Fandango";
  return source;
}

function sourceBadge(source: string): string {
  if (source.startsWith("amc")) return "badge-info";
  if (source.startsWith("fandango")) return "badge-warning";
  return "badge-info";
}

export default function TicketFindings({ findings }: Props) {
  return (
    <div className="card">
      <div className="flex items-center gap-3 mb-4">
        <span className="text-xl">🎟️</span>
        <h2 className="text-lg font-semibold text-slate-200">Ticket Findings</h2>
        <span className="badge-info">{findings.length}</span>
      </div>

      {findings.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-4xl mb-3">🔍</p>
          <p className="text-slate-400 font-medium">No tickets found yet</p>
          <p className="text-slate-600 text-sm mt-1">
            The bot is monitoring and will alert you as soon as tickets drop.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {findings.map((f) => (
            <div
              key={f.id}
              className="flex items-center gap-4 bg-cinema-800/60 rounded-xl p-4 border border-cinema-600/30 hover:border-gold-500/30 transition-colors"
            >
              <div className="shrink-0">
                <span className={sourceBadge(f.source)}>
                  {sourceLabel(f.source)}
                </span>
              </div>

              <div className="flex-1 min-w-0">
                <p className="font-medium text-slate-200 truncate">
                  {f.theatre_name}
                </p>
                <p className="text-sm text-slate-400 mt-0.5">
                  {f.movie_name} &middot; {f.show_date}
                  {f.showtime && <> &middot; {f.showtime}</>}
                </p>
              </div>

              <div className="shrink-0 text-right">
                <p className="text-xs text-slate-500">
                  {new Date(f.found_at).toLocaleString()}
                </p>
                {f.ticket_url && (
                  <a
                    href={f.ticket_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-block mt-1 text-xs font-semibold text-gold-500 hover:text-gold-400 transition-colors"
                  >
                    View / Buy →
                  </a>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
