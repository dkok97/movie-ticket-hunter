export interface BotStatus {
  is_checking: boolean;
  scheduler_running: boolean;
  poll_interval_minutes: number;
  next_check: string | null;
  last_run: BotRun | null;
  movie_name: string;
  movie_year: string;
  movie_format: string;
  target_date: string;
  start_date: string;
  end_date: string;
  dates_tracked: number;
  total_findings: number;
  total_runs: number;
}

export interface BotRun {
  id: number;
  started_at: string;
  completed_at: string | null;
  status: "running" | "completed" | "error";
  sources_checked: string | null;
  tickets_found: number;
  details: string | null;
  error: string | null;
}

export interface TicketFinding {
  id: number;
  run_id: number;
  source: string;
  theatre_name: string;
  movie_name: string;
  showtime: string;
  show_date: string;
  ticket_url: string;
  found_at: string;
}

export interface Theatre {
  name: string;
  source: string;
  slug: string;
  region?: string;
  theatre_id: string;
}

export interface BotConfig {
  movie_name: string;
  movie_year: string;
  movie_format: string;
  target_date: string;
  start_date: string;
  end_date: string;
  poll_interval_minutes: number;
  ntfy_topic: string;
  ntfy_server: string;
  notifications_enabled: boolean;
  amc_api_key: string;
  amc_vendor_id: string;
  amc_movie_slug: string;
  purchase_theatre: string;
  num_tickets: number;
  evening_start_hour: number;
  theatres: Theatre[];
}
