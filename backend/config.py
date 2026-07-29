"""Configuration management for the Odyssey IMAX 70mm Ticket Hunter."""

import json
import os
from datetime import date, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------
#
# Target: Christopher Nolan's "The Odyssey" in the IMAX 70mm film format.
# In NYC this format only exists at AMC Lincoln Square 13. There is only one
# AMC movie page — slug "the-odyssey-76238" — and it lists every format the
# movie plays in at a given theatre. AMC's own site filters to a specific
# premium format via the "premium-offering" query param (e.g. "imax70mm"),
# and needs an explicit "theatre" query param too, since a headless browser
# has no saved location and AMC otherwise shows a "select a theatre" prompt
# instead of showtimes. (The slug "the-odyssey-80679" that used to be
# configured here was a stale/incorrect slug that 500s on AMC's site.)
#
# Shows are mostly sold out and run 4-5x/day (including 2 a.m. screenings)
# for several weeks, so we sweep a date *range* and report only showtimes
# that actually have open seats.

DEFAULT_CONFIG: dict = {
    "movie_name": "The Odyssey",
    "movie_year": "2026",
    "movie_format": "IMAX 70mm",  # premium format we care about
    # Date range to sweep (inclusive). get_date_range() clamps the start to
    # today if this is in the past, so this only matters as a lower bound.
    "start_date": "2026-07-20",
    "end_date": "2026-08-20",
    # target_date kept for backward compatibility / single-date fallback.
    "target_date": "2026-07-20",
    "poll_interval_minutes": 2,
    "ntfy_topic": os.getenv("NTFY_TOPIC", "odyssey-imax70mm-2"),
    "ntfy_server": os.getenv("NTFY_SERVER", "https://ntfy.sh"),
    "notifications_enabled": True,
    "amc_api_key": os.getenv("AMC_API_KEY", ""),
    "amc_vendor_id": os.getenv("AMC_VENDOR_ID", ""),
    "amc_movie_slug": "the-odyssey-76238",  # from amctheatres.com/movies/{slug}
    # AMC's "premium-offering" query param — filters the showtimes page down
    # to just this format (e.g. "imax70mm"). Empty means no format filter.
    "amc_premium_offering": "imax70mm",
    "purchase_theatre": "AMC Lincoln Square 13",  # theatre to highlight with 1-click purchase links
    # Minimum open-seat threshold to alert on (1 = any single open seat is
    # worth a notification) — also how many seats auto_hold_seats will try
    # to reserve when it creates a hold.
    "num_tickets": 1,
    # Automatically drive AMC's guest checkout (seats -> tickets -> order)
    # for each newly-found showtime, so the notification links straight to a
    # real, already-held order instead of just a showtime page. No login, no
    # payment info — you still complete checkout yourself, just faster.
    "auto_hold_seats": True,
    # 0 = report every showtime of the day (the 70mm shows include 2-3 a.m.
    # screenings that we do NOT want to filter out).
    "evening_start_hour": 0,
    # Seat-level detail (requires reading the seat map).
    "report_seats": True,            # include open seat labels in notifications
    "exclude_accessible_seats": True,  # ignore wheelchair/companion seats
    # Only alert when the seat map confirms enough non-accessible open seats.
    # Unverified / ADA-only / short-handed shows are dropped (fail-closed) —
    # AMC's showtimes page marks ADA-only shows as "bookable", so trusting it
    # without a seat map produces a flood of false alerts.
    "require_standard_seats": True,
    # Use a headless browser (Playwright) to fetch AMC when the API is
    # unavailable — required to get past AMC's Queue-it waiting room.
    "use_browser": True,
    # How many dates to check concurrently per cycle.
    "max_concurrency": 4,
    # Check the first N days of the range to completion *before* the rest, so
    # near-term cancellations are found (and held) minutes earlier in a cycle.
    "priority_days": 3,
    # Cap guest-checkout holds per cycle — each hold burns proxy + ~30–60s.
    # Rank by soonest show_date; remaining findings still get seat-map alerts.
    "max_holds_per_cycle": 2,
    # Max seat-map lookups per date (each is a browser round-trip). High enough
    # to verify every showtime of a day; overall browser load is bounded by the
    # global browser concurrency cap (BROWSER_CONCURRENCY env, default 4).
    "max_seat_lookups": 100,
    "theatres": json.dumps([
        {
            "name": "AMC Lincoln Square 13",
            "source": "amc",
            "slug": "amc-lincoln-square-13",
            "region": "new-york-city",
            "theatre_id": "1842",
        },
    ]),
}

# ---------------------------------------------------------------------------
# Runtime config helpers
# ---------------------------------------------------------------------------

_runtime_config: dict = {}


def load_config():
    """Load config from DB, falling back to defaults."""
    from database import get_all_config  # noqa: late import to avoid circular

    global _runtime_config
    db_cfg = get_all_config()
    _runtime_config = {**DEFAULT_CONFIG, **db_cfg}


def get_config() -> dict:
    if not _runtime_config:
        load_config()
    return dict(_runtime_config)


def get(key: str, default=None):
    cfg = get_config()
    return cfg.get(key, default)


def get_typed(key: str, default=None):
    """Get a config value, attempting to deserialise JSON."""
    raw = get(key, default)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
    return raw


def update_config(updates: dict):
    """Persist config updates to DB and refresh runtime config."""
    from database import set_all_config  # noqa: late import

    set_all_config(updates)
    load_config()


def get_theatres() -> list[dict]:
    return get_typed("theatres", [])


def get_date_range() -> list[str]:
    """Return the inclusive list of ISO dates to check.

    Uses start_date/end_date when present, otherwise falls back to the
    single target_date. If start_date has drifted into the past, it's
    clamped to today — there's no point checking showtimes that have already
    happened. Capped at 60 days to avoid runaway request volume.
    """
    conf = get_config()
    start = conf.get("start_date") or conf.get("target_date") or ""
    end = conf.get("end_date") or start

    try:
        s = date.fromisoformat(str(start))
        e = date.fromisoformat(str(end))
    except (ValueError, TypeError):
        return [start] if start else []

    today = date.today()
    if s < today:
        s = today

    if e < s:
        e = s

    span = min((e - s).days, 60)
    return [(s + timedelta(days=i)).isoformat() for i in range(span + 1)]
