import type { BotStatus, BotRun, TicketFinding, BotConfig } from "./types";

// In production (Vercel), VITE_API_URL points to the Railway backend.
// In local dev, the Vite proxy forwards /api to localhost:8000.
const API_URL = import.meta.env.VITE_API_URL || "";
const BASE = `${API_URL}/api`;

async function fetchJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const fullUrl = `${BASE}${url}`;
  let res: Response;
  try {
    res = await fetch(fullUrl, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch (e) {
    throw new Error(
      API_URL
        ? `Cannot reach backend at ${API_URL}. Is it running?`
        : "Backend URL not configured. Set VITE_API_URL environment variable."
    );
  }
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body.slice(0, 200)}`);
  }
  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error(
      API_URL
        ? `Backend returned HTML instead of JSON. Check VITE_API_URL (${API_URL}).`
        : "Got HTML instead of JSON. Set VITE_API_URL to your Railway backend URL."
    );
  }
  return res.json();
}

export const api = {
  getStatus: () => fetchJSON<BotStatus>("/status"),
  getRuns: (limit = 30) => fetchJSON<BotRun[]>(`/runs?limit=${limit}`),
  getFindings: (limit = 50) => fetchJSON<TicketFinding[]>(`/findings?limit=${limit}`),
  getConfig: () => fetchJSON<BotConfig>("/config"),

  updateConfig: (cfg: Partial<BotConfig>) =>
    fetchJSON<{ status: string }>("/config", {
      method: "PUT",
      body: JSON.stringify(cfg),
    }),

  triggerCheck: () =>
    fetchJSON<{ status: string; message: string }>("/trigger", { method: "POST" }),

  testNotification: () =>
    fetchJSON<{ status: string; message: string }>("/test-notification", { method: "POST" }),

  clearHistory: () =>
    fetchJSON<{ status: string; message: string }>("/history", { method: "DELETE" }),
};
