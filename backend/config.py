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
# In NYC this format only exists at AMC Lincoln Square 13. AMC publishes a
# format-specific movie page for the event — slug "the-odyssey-80679"
# ("The Odyssey – IMAX 70mm Event") — which is separate from the standard
# digital release ("the-odyssey-76238"). We watch the 70mm page so every
# showtime we see is already the right format.
#
# Shows are mostly sold out and run 4-5x/day (including 2 a.m. screenings)
# for several weeks, so we sweep a date *range* and report only showtimes
# that actually have open seats.

DEFAULT_CONFIG: dict = {
    "movie_name": "The Odyssey",
    "movie_year": "2026",
    "movie_format": "IMAX 70mm",  # premium format we care about
    # Date range to sweep (inclusive). "some weeks" of coverage.
    "start_date": "2026-07-20",
    "end_date": "2026-08-10",
    # target_date kept for backward compatibility / single-date fallback.
    "target_date": "2026-07-20",
    "poll_interval_minutes": 2,
    "ntfy_topic": os.getenv("NTFY_TOPIC", "odyssey-imax-70mm"),
    "ntfy_server": os.getenv("NTFY_SERVER", "https://ntfy.sh"),
    "notifications_enabled": True,
    "amc_api_key": os.getenv("AMC_API_KEY", ""),
    "amc_vendor_id": os.getenv("AMC_VENDOR_ID", ""),
    # The IMAX 70mm event page — every showtime here is already 70mm.
    "amc_movie_slug": "the-odyssey-80679",  # from amctheatres.com/movies/{slug}
    "purchase_theatre": "AMC Lincoln Square 13",  # theatre to highlight with 1-click purchase links
    "num_tickets": 2,  # number of tickets to buy
    # 0 = report every showtime of the day (the 70mm shows include 2-3 a.m.
    # screenings that we do NOT want to filter out).
    "evening_start_hour": 0,
    # Seat-level detail (requires AMC API access to seat data).
    "report_seats": True,            # include open seat labels in notifications
    "exclude_accessible_seats": True,  # ignore wheelchair/companion seats
    # Use a headless browser (Playwright) to fetch AMC when the API is
    # unavailable — required to get past AMC's Queue-it waiting room.
    "use_browser": True,
    # How many dates to check concurrently per cycle.
    "max_concurrency": 4,
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
    single target_date. Capped at 60 days to avoid runaway request volume.
    """
    conf = get_config()
    start = conf.get("start_date") or conf.get("target_date") or ""
    end = conf.get("end_date") or start

    try:
        s = date.fromisoformat(str(start))
        e = date.fromisoformat(str(end))
    except (ValueError, TypeError):
        return [start] if start else []

    if e < s:
        e = s

    span = min((e - s).days, 60)
    return [(s + timedelta(days=i)).isoformat() for i in range(span + 1)]
