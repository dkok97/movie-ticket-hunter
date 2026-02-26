import { useState, useEffect, useCallback } from "react";
import type { BotStatus, BotRun, TicketFinding, BotConfig } from "./types";
import { api } from "./api";
import Header from "./components/Header";
import Dashboard from "./components/Dashboard";
import TicketFindings from "./components/TicketFindings";
import RunHistory from "./components/RunHistory";
import ConfigPanel from "./components/ConfigPanel";

export default function App() {
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [runs, setRuns] = useState<BotRun[]>([]);
  const [findings, setFindings] = useState<TicketFinding[]>([]);
  const [config, setConfig] = useState<BotConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [activeTab, setActiveTab] = useState<"dashboard" | "history" | "settings">("dashboard");

  const refresh = useCallback(async () => {
    try {
      const [s, r, f] = await Promise.all([
        api.getStatus(),
        api.getRuns(30),
        api.getFindings(50),
      ]);
      setStatus(s);
      setRuns(r);
      setFindings(f);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to fetch data");
    }
  }, []);

  const refreshConfig = useCallback(async () => {
    try {
      const c = await api.getConfig();
      setConfig(c);
    } catch {
      /* config fetch failure is non-critical */
    }
  }, []);

  useEffect(() => {
    refresh();
    refreshConfig();
    const id = setInterval(refresh, 10_000); // refresh every 10s
    return () => clearInterval(id);
  }, [refresh, refreshConfig]);

  const handleTrigger = async () => {
    setTriggering(true);
    try {
      await api.triggerCheck();
      // Wait a moment then refresh to show the new run
      setTimeout(refresh, 2000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to trigger check");
    } finally {
      setTriggering(false);
    }
  };

  const handleConfigSave = async (updates: Partial<BotConfig>) => {
    await api.updateConfig(updates);
    await refreshConfig();
    await refresh();
  };

  return (
    <div className="min-h-screen">
      <Header status={status} />

      {/* Connection error banner */}
      {error && (
        <div className="max-w-6xl mx-auto px-4 mt-4">
          <div className="bg-red-500/10 border border-red-500/30 rounded-xl px-4 py-3 flex items-center gap-3">
            <span className="text-red-400 text-lg">⚠️</span>
            <span className="text-red-300 text-sm">{error}</span>
            <button onClick={refresh} className="ml-auto text-red-400 hover:text-red-300 text-sm underline">
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Navigation tabs */}
      <div className="max-w-6xl mx-auto px-4 mt-6">
        <nav className="flex gap-1 bg-cinema-800/50 p-1 rounded-xl w-fit">
          {(["dashboard", "history", "settings"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-5 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                activeTab === tab
                  ? "bg-gold-500 text-cinema-900 shadow-lg shadow-gold-500/20"
                  : "text-slate-400 hover:text-slate-200 hover:bg-cinema-700/50"
              }`}
            >
              {tab === "dashboard" && "📊 Dashboard"}
              {tab === "history" && "📋 History"}
              {tab === "settings" && "⚙️ Settings"}
            </button>
          ))}
        </nav>
      </div>

      {/* Content */}
      <main className="max-w-6xl mx-auto px-4 py-6 space-y-6 animate-fade-in">
        {activeTab === "dashboard" && (
          <>
            <Dashboard
              status={status}
              triggering={triggering}
              onTrigger={handleTrigger}
              onRefresh={refresh}
            />
            <TicketFindings findings={findings} />
            <RunHistory runs={runs.slice(0, 10)} onClear={refresh} />
          </>
        )}

        {activeTab === "history" && <RunHistory runs={runs} expanded onClear={refresh} />}

        {activeTab === "settings" && (
          <ConfigPanel config={config} onSave={handleConfigSave} onRefresh={refreshConfig} />
        )}
      </main>

      {/* Footer */}
      <footer className="max-w-6xl mx-auto px-4 py-8 text-center text-slate-600 text-xs">
        Dhurandhar Ticket Hunter &middot; Watching for ticket drops so you don't have to.
      </footer>
    </div>
  );
}
