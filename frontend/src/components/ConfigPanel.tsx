import { useState, useEffect } from "react";
import type { BotConfig, Theatre } from "../types";

interface Props {
  config: BotConfig | null;
  onSave: (updates: Partial<BotConfig>) => Promise<void>;
  onRefresh: () => void;
}

export default function ConfigPanel({ config, onSave, onRefresh }: Props) {
  const [form, setForm] = useState<Partial<BotConfig>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [theatreForm, setTheatreForm] = useState<Theatre>({
    name: "",
    source: "amc",
    slug: "",
    region: "",
    theatre_id: "",
  });

  useEffect(() => {
    if (config) {
      setForm({ ...config });
    }
  }, [config]);

  const handleChange = (key: keyof BotConfig, value: string | number | boolean) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(form);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  };

  const addTheatre = () => {
    if (!theatreForm.name || !theatreForm.slug) return;
    const current = (form.theatres ?? config?.theatres ?? []) as Theatre[];
    const updated = [...current, { ...theatreForm }];
    setForm((prev) => ({ ...prev, theatres: updated }));
    setTheatreForm({ name: "", source: "amc", slug: "", region: "", theatre_id: "" });
  };

  const removeTheatre = (idx: number) => {
    const current = (form.theatres ?? config?.theatres ?? []) as Theatre[];
    const updated = current.filter((_, i) => i !== idx);
    setForm((prev) => ({ ...prev, theatres: updated }));
  };

  if (!config) {
    return (
      <div className="card text-center py-12">
        <p className="text-slate-500">Loading configuration...</p>
      </div>
    );
  }

  const theatres = (form.theatres ?? config.theatres ?? []) as Theatre[];

  return (
    <div className="space-y-6">
      {/* Movie Settings */}
      <div className="card">
        <div className="flex items-center gap-3 mb-6">
          <span className="text-xl">🎬</span>
          <h2 className="text-lg font-semibold text-slate-200">Movie Settings</h2>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <Field label="Movie Name">
            <input
              className="input-field"
              value={form.movie_name ?? ""}
              onChange={(e) => handleChange("movie_name", e.target.value)}
              placeholder="The Odyssey"
            />
          </Field>
          <Field label="Movie Year">
            <input
              className="input-field"
              value={form.movie_year ?? ""}
              onChange={(e) => handleChange("movie_year", e.target.value)}
              placeholder="2026"
            />
          </Field>
          <Field label="Format (premium format filter)">
            <input
              className="input-field"
              value={form.movie_format ?? ""}
              onChange={(e) => handleChange("movie_format", e.target.value)}
              placeholder="IMAX 70mm"
            />
          </Field>
          <Field label="Start Date">
            <input
              className="input-field"
              type="date"
              value={form.start_date ?? ""}
              onChange={(e) => handleChange("start_date", e.target.value)}
            />
          </Field>
          <Field label="End Date">
            <input
              className="input-field"
              type="date"
              value={form.end_date ?? ""}
              onChange={(e) => handleChange("end_date", e.target.value)}
            />
          </Field>
          <Field label="Earliest Show Hour (0 = all shows)">
            <input
              className="input-field"
              type="number"
              min={0}
              max={23}
              value={form.evening_start_hour ?? ""}
              onChange={(e) => {
                const v = e.target.value;
                handleChange("evening_start_hour", v === "" ? ("" as any) : parseInt(v));
              }}
            />
          </Field>
        </div>
        <p className="text-xs text-slate-500 mt-3">
          The bot sweeps every date from <strong className="text-slate-400">Start</strong> to{" "}
          <strong className="text-slate-400">End</strong> each cycle. Set{" "}
          <strong className="text-slate-400">Earliest Show Hour</strong> to <code>0</code> to include the
          overnight 2&nbsp;a.m. IMAX 70mm screenings.
        </p>
      </div>

      {/* Purchase Settings */}
      <div className="card">
        <div className="flex items-center gap-3 mb-6">
          <span className="text-xl">🛒</span>
          <h2 className="text-lg font-semibold text-slate-200">1-Click Purchase</h2>
        </div>

        <div className="bg-cinema-800/60 rounded-xl p-4 border border-cinema-600/30 mb-4">
          <p className="text-sm text-slate-400">
            <strong className="text-slate-300">How it works:</strong> When tickets are found at your
            priority theatre, you'll get a <strong className="text-emerald-400">max-priority</strong> notification
            with a <strong className="text-gold-400">BUY NOW</strong> button that opens the AMC purchase page instantly.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Field label="Priority Theatre (for purchase alerts)">
            <select
              className="input-field"
              value={form.purchase_theatre ?? "AMC Lincoln Square 13"}
              onChange={(e) => handleChange("purchase_theatre", e.target.value)}
            >
              {theatres.map((t, idx) => (
                <option key={idx} value={t.name}>
                  {t.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Number of Tickets">
            <input
              className="input-field"
              type="number"
              min={1}
              max={20}
              value={form.num_tickets ?? ""}
              onChange={(e) => {
                const v = e.target.value;
                handleChange("num_tickets", v === "" ? ("" as any) : parseInt(v));
              }}
            />
          </Field>
        </div>
      </div>

      {/* Bot Settings */}
      <div className="card">
        <div className="flex items-center gap-3 mb-6">
          <span className="text-xl">🤖</span>
          <h2 className="text-lg font-semibold text-slate-200">Bot Settings</h2>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <Field label="Poll Interval (minutes)">
            <input
              className="input-field"
              type="number"
              min={1}
              max={1440}
              value={form.poll_interval_minutes ?? ""}
              onChange={(e) => {
                const v = e.target.value;
                handleChange("poll_interval_minutes", v === "" ? ("" as any) : parseInt(v));
              }}
            />
          </Field>
          <Field label="AMC API Key (optional)">
            <input
              className="input-field font-mono text-xs"
              type="password"
              value={form.amc_api_key ?? ""}
              onChange={(e) => handleChange("amc_api_key", e.target.value)}
              placeholder="Leave empty to use web scraping"
            />
          </Field>
          <Field label="AMC Vendor ID (optional)">
            <input
              className="input-field font-mono text-xs"
              type="password"
              value={form.amc_vendor_id ?? ""}
              onChange={(e) => handleChange("amc_vendor_id", e.target.value)}
              placeholder="From AMC Developer Portal"
            />
          </Field>
          <Field label="AMC Movie Slug">
            <input
              className="input-field font-mono text-xs"
              value={form.amc_movie_slug ?? ""}
              onChange={(e) => handleChange("amc_movie_slug", e.target.value)}
              placeholder="the-odyssey-80679"
            />
            <p className="text-xs text-slate-500 mt-1">
              From amctheatres.com/movies/<strong className="text-slate-400">{form.amc_movie_slug || "slug"}</strong>
              {" "}— use the <strong className="text-slate-400">IMAX 70mm Event</strong> page so every
              showtime is already the right format.
            </p>
          </Field>
        </div>
      </div>

      {/* Notification Settings */}
      <div className="card">
        <div className="flex items-center gap-3 mb-6">
          <span className="text-xl">🔔</span>
          <h2 className="text-lg font-semibold text-slate-200">Notifications</h2>
          <a
            href="https://ntfy.sh"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-gold-500 hover:text-gold-400 ml-auto"
          >
            What is ntfy.sh? →
          </a>
        </div>

        <div className="bg-cinema-800/60 rounded-xl p-4 border border-cinema-600/30 mb-4">
          <p className="text-sm text-slate-400">
            <strong className="text-slate-300">How to get notifications:</strong>
          </p>
          <ol className="text-sm text-slate-400 list-decimal list-inside mt-2 space-y-1">
            <li>
              Install the <strong className="text-slate-300">ntfy</strong> app on your phone
              (iOS / Android) or use the web at{" "}
              <a href="https://ntfy.sh" target="_blank" className="text-gold-500 hover:underline">
                ntfy.sh
              </a>
            </li>
            <li>
              Subscribe to the topic:{" "}
              <code className="text-gold-400 bg-cinema-900 px-2 py-0.5 rounded font-mono text-xs">
                {form.ntfy_topic || "odyssey-imax-70mm"}
              </code>
            </li>
            <li>Done! You'll get push notifications when tickets drop 🎉</li>
          </ol>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Field label="ntfy Topic">
            <input
              className="input-field font-mono"
              value={form.ntfy_topic ?? ""}
              onChange={(e) => handleChange("ntfy_topic", e.target.value)}
              placeholder="odyssey-imax-70mm"
            />
          </Field>
          <Field label="ntfy Server">
            <input
              className="input-field"
              value={form.ntfy_server ?? ""}
              onChange={(e) => handleChange("ntfy_server", e.target.value)}
              placeholder="https://ntfy.sh"
            />
          </Field>
          <Field label="Notifications Enabled">
            <button
              onClick={() =>
                handleChange("notifications_enabled", !(form.notifications_enabled ?? true))
              }
              className={`w-full py-2.5 rounded-xl font-medium transition-all duration-200 ${
                form.notifications_enabled ?? true
                  ? "bg-emerald-500/20 border border-emerald-500/40 text-emerald-400"
                  : "bg-cinema-800 border border-cinema-600 text-slate-500"
              }`}
            >
              {(form.notifications_enabled ?? true) ? "✅ Enabled" : "Disabled"}
            </button>
          </Field>
        </div>
      </div>

      {/* Theatres */}
      <div className="card">
        <div className="flex items-center gap-3 mb-6">
          <span className="text-xl">🎭</span>
          <h2 className="text-lg font-semibold text-slate-200">Theatres to Monitor</h2>
        </div>

        <div className="space-y-3 mb-6">
          {theatres.map((t, idx) => (
            <div
              key={idx}
              className="flex items-center gap-3 bg-cinema-800/60 rounded-xl p-4 border border-cinema-600/30"
            >
              <span
                className={`badge ${
                  t.source === "amc" ? "badge-info" : "badge-warning"
                }`}
              >
                {t.source.toUpperCase()}
              </span>
              <div className="flex-1 min-w-0">
                <p className="font-medium text-slate-300 truncate">{t.name}</p>
                <p className="text-xs text-slate-500 font-mono truncate">
                  {t.slug}
                  {t.theatre_id && ` · ID: ${t.theatre_id}`}
                </p>
              </div>
              <button
                onClick={() => removeTheatre(idx)}
                className="text-red-400 hover:text-red-300 text-sm transition-colors"
              >
                ✕
              </button>
            </div>
          ))}
        </div>

        {/* Add theatre form */}
        <div className="bg-cinema-800/40 rounded-xl p-4 border border-dashed border-cinema-500/40">
          <p className="text-sm text-slate-400 mb-3 font-medium">Add a theatre</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            <input
              className="input-field text-sm"
              value={theatreForm.name}
              onChange={(e) => setTheatreForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="Theatre name"
            />
            <select
              className="input-field text-sm"
              value={theatreForm.source}
              onChange={(e) => setTheatreForm((f) => ({ ...f, source: e.target.value }))}
            >
              <option value="amc">AMC</option>
              <option value="fandango">Fandango</option>
            </select>
            <input
              className="input-field text-sm font-mono"
              value={theatreForm.slug}
              onChange={(e) => setTheatreForm((f) => ({ ...f, slug: e.target.value }))}
              placeholder="url-slug"
            />
            <input
              className="input-field text-sm font-mono"
              value={theatreForm.region ?? ""}
              onChange={(e) => setTheatreForm((f) => ({ ...f, region: e.target.value }))}
              placeholder="region (e.g. new-york-city)"
            />
            <input
              className="input-field text-sm font-mono"
              value={theatreForm.theatre_id}
              onChange={(e) => setTheatreForm((f) => ({ ...f, theatre_id: e.target.value }))}
              placeholder="ID (optional)"
            />
          </div>
          <button onClick={addTheatre} className="btn-secondary mt-3 text-sm">
            + Add Theatre
          </button>
        </div>
      </div>

      {/* Save bar */}
      <div className="card flex items-center justify-between">
        <p className="text-sm text-slate-500">
          Save changes to update the bot configuration.
        </p>
        <div className="flex items-center gap-3">
          <button onClick={onRefresh} className="btn-secondary text-sm">
            Reset
          </button>
          <button onClick={handleSave} disabled={saving} className="btn-primary">
            {saving ? "Saving..." : saved ? "✅ Saved!" : "💾 Save Settings"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-slate-400 uppercase tracking-wider mb-1.5 block">
        {label}
      </span>
      {children}
    </label>
  );
}
