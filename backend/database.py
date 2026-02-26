"""SQLite database layer for the Dhurandhar Ticket Hunter bot."""

import os
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

# Use DATA_DIR env var for Railway volume mount, default to local directory
_data_dir = Path(os.environ.get("DATA_DIR", str(Path(__file__).parent)))
DB_PATH = _data_dir / "dhurandhar.db"


def _conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bot_runs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at      TEXT    NOT NULL,
            completed_at    TEXT,
            status          TEXT    NOT NULL DEFAULT 'running',
            sources_checked TEXT,
            tickets_found   INTEGER DEFAULT 0,
            details         TEXT,
            error           TEXT
        );

        CREATE TABLE IF NOT EXISTS ticket_findings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          INTEGER NOT NULL,
            source          TEXT    NOT NULL,
            theatre_name    TEXT    NOT NULL,
            movie_name      TEXT    NOT NULL,
            showtime        TEXT,
            show_date       TEXT,
            ticket_url      TEXT,
            found_at        TEXT    NOT NULL,
            FOREIGN KEY (run_id) REFERENCES bot_runs(id)
        );

        CREATE TABLE IF NOT EXISTS config (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Bot runs
# ---------------------------------------------------------------------------

def create_run() -> int:
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO bot_runs (started_at, status) VALUES (?, ?)",
        (datetime.now(timezone.utc).isoformat(), "running"),
    )
    run_id = cur.lastrowid
    conn.commit()
    conn.close()
    return run_id


def complete_run(run_id: int, sources_checked: list[str], tickets_found: int, details: str):
    conn = _conn()
    conn.execute(
        """UPDATE bot_runs
           SET completed_at = ?, status = 'completed',
               sources_checked = ?, tickets_found = ?, details = ?
           WHERE id = ?""",
        (
            datetime.now(timezone.utc).isoformat(),
            json.dumps(sources_checked),
            tickets_found,
            details,
            run_id,
        ),
    )
    conn.commit()
    conn.close()


def fail_run(run_id: int, error: str):
    conn = _conn()
    conn.execute(
        """UPDATE bot_runs
           SET completed_at = ?, status = 'error', error = ?
           WHERE id = ?""",
        (datetime.now(timezone.utc).isoformat(), error, run_id),
    )
    conn.commit()
    conn.close()


def get_runs(limit: int = 50, offset: int = 0) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM bot_runs ORDER BY id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_run(run_id: int) -> dict | None:
    conn = _conn()
    row = conn.execute("SELECT * FROM bot_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Ticket findings
# ---------------------------------------------------------------------------

def save_findings(run_id: int, findings: list[dict]):
    conn = _conn()
    for f in findings:
        conn.execute(
            """INSERT INTO ticket_findings
               (run_id, source, theatre_name, movie_name, showtime, show_date, ticket_url, found_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                f.get("source", ""),
                f.get("theatre_name", ""),
                f.get("movie_name", ""),
                f.get("showtime", ""),
                f.get("show_date", ""),
                f.get("ticket_url", ""),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    conn.commit()
    conn.close()


def get_findings(limit: int = 100) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM ticket_findings ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_history():
    """Delete all bot runs and ticket findings."""
    conn = _conn()
    conn.execute("DELETE FROM ticket_findings")
    conn.execute("DELETE FROM bot_runs")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def get_config_value(key: str, default: str = "") -> str:
    conn = _conn()
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_config_value(key: str, value: str):
    conn = _conn()
    conn.execute(
        """INSERT INTO config (key, value, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (key, value, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_all_config() -> dict:
    conn = _conn()
    rows = conn.execute("SELECT key, value FROM config").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def set_all_config(cfg: dict):
    for k, v in cfg.items():
        set_config_value(k, v if isinstance(v, str) else json.dumps(v))
