"""Configuration management for the Dhurandhar Ticket Hunter."""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict = {
    "movie_name": "Dhurandhar Revenge",
    "movie_year": "2026",
    "target_date": "2026-03-19",
    "poll_interval_minutes": 1,
    "ntfy_topic": os.getenv("NTFY_TOPIC", "dhurandhar-tickets"),
    "ntfy_server": os.getenv("NTFY_SERVER", "https://ntfy.sh"),
    "notifications_enabled": True,
    "amc_api_key": os.getenv("AMC_API_KEY", ""),
    "amc_vendor_id": os.getenv("AMC_VENDOR_ID", ""),
    "amc_movie_slug": "dhurandhar-the-revenge-83029",  # from amctheatres.com/movies/{slug}
    "purchase_theatre": "AMC Empire 25",  # theatre to highlight with 1-click purchase links
    "num_tickets": 2,  # number of tickets to buy
    "evening_start_hour": 16,  # 4 PM and later = evening show
    "theatres": json.dumps([
        {
            "name": "AMC Empire 25",
            "source": "amc",
            "slug": "amc-empire-25",
            "theatre_id": "1552",
        },
        {
            "name": "AMC Lincoln Square 13",
            "source": "amc",
            "slug": "amc-lincoln-square-13",
            "theatre_id": "1842",
        },
        {
            "name": "AMC Kips Bay 15",
            "source": "amc",
            "slug": "amc-kips-bay-15",
            "theatre_id": "2310",
        },
        {
            "name": "Regal Union Square",
            "source": "fandango",
            "slug": "regal-union-square-stadium-14-rpx-imax-aayjt",
            "theatre_id": "",
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
