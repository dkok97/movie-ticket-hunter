"""Odyssey IMAX 70mm Ticket Hunter — FastAPI backend.

Periodically checks AMC (and Fandango) for open seats to Christopher Nolan's
"The Odyssey" in IMAX 70mm at AMC Lincoln Square, sweeping a range of dates,
and sends push notifications via ntfy.sh the moment seats open up.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config as cfg
import database as db
import checker
import notifier

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-24s  %(levelname)-7s  %(message)s",
)
log = logging.getLogger("odyssey.main")

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

scheduler = AsyncIOScheduler()
_is_running = False  # guard to prevent concurrent runs

# Max wall-clock for one check cycle before the watchdog aborts it.
# Generous because verifying seats renders one browser page per show, bounded
# by the global browser concurrency cap.
CHECK_TIMEOUT_SECONDS = 900

# Signatures of showtimes we've already alerted on. We only notify when a
# showtime *newly* has open seats. If a showtime sells out and later opens
# again, its signature drops out of this set and we re-alert.
_notified_signatures: set[str] = set()


def _finding_signature(f: dict) -> str:
    """Stable key for a showtime: date + time + theatre."""
    return "|".join([
        str(f.get("show_date", "")),
        str(f.get("showtime", "")),
        str(f.get("theatre_name", "")),
    ])


async def scheduled_check():
    """Runs as a scheduled job — perform a full ticket check cycle."""
    global _is_running
    if _is_running:
        log.info("Check already in progress, skipping this cycle.")
        return

    _is_running = True
    run_id = db.create_run()

    try:
        log.info("=== Starting scheduled check (run #%d) ===", run_id)
        # Watchdog: never let a single cycle hang forever (e.g. a stalled
        # request), which would otherwise block all future cycles.
        findings, sources, details = await asyncio.wait_for(
            checker.run_all_checks(), timeout=CHECK_TIMEOUT_SECONDS
        )

        db.complete_run(run_id, sources, len(findings), details)

        if findings:
            db.save_findings(run_id, findings)

            # Only notify for showtimes we haven't already alerted on.
            current_sigs = {_finding_signature(f) for f in findings}
            new_findings = [
                f for f in findings if _finding_signature(f) not in _notified_signatures
            ]
            # Forget showtimes that are no longer available so they re-alert
            # if seats open up again later.
            _notified_signatures.intersection_update(current_sigs)

            if new_findings:
                # Defense in depth: never alert on unverified showtimes when we
                # require confirmed standard seats (legacy fail-open paths).
                require_standard = str(
                    cfg.get("require_standard_seats", True)
                ).lower() not in ("false", "0", "no")
                if require_standard:
                    verified = [
                        f for f in new_findings
                        if f.get("available_seats")
                        and "(seats unverified)" not in str(f.get("seat_summary", ""))
                    ]
                    skipped = len(new_findings) - len(verified)
                    if skipped:
                        log.warning(
                            "Skipping notify for %d unverified finding(s)", skipped
                        )
                    new_findings = verified

            if new_findings:
                _notified_signatures.update(_finding_signature(f) for f in new_findings)
                log.info(
                    "🎟️  %d open showtime(s), %d new! Sending notification...",
                    len(findings), len(new_findings),
                )
                await notifier.send_ticket_alert(new_findings)
            else:
                log.info(
                    "%d open showtime(s), all previously alerted or unverified — no new notification.",
                    len(findings),
                )
        else:
            log.info("No open seats found this cycle.")

    except Exception as exc:
        log.exception("Check failed: %s", exc)
        db.fail_run(run_id, str(exc))
    finally:
        db.gc_runs(keep=100)
        _is_running = False


def _reschedule(minutes: int):
    """Reschedule the periodic job with a new interval."""
    try:
        scheduler.remove_job("ticket_check")
    except Exception:
        pass
    scheduler.add_job(
        scheduled_check,
        "interval",
        minutes=max(1, minutes),
        id="ticket_check",
        replace_existing=True,
    )
    log.info("Scheduler set to run every %d minute(s)", minutes)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    cfg.load_config()

    interval = int(cfg.get("poll_interval_minutes", 1))
    _reschedule(interval)
    scheduler.start()
    log.info("🎬 Odyssey IMAX 70mm Ticket Hunter started!")

    yield

    scheduler.shutdown(wait=False)
    try:
        import browser
        await browser.shutdown()
    except Exception:
        pass
    log.info("Scheduler shut down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Odyssey IMAX 70mm Ticket Hunter",
    description="Watches AMC for open IMAX 70mm seats to The Odyssey",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ConfigUpdate(BaseModel):
    movie_name: str | None = None
    movie_year: str | None = None
    movie_format: str | None = None
    target_date: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    poll_interval_minutes: int | None = None
    ntfy_topic: str | None = None
    ntfy_server: str | None = None
    notifications_enabled: bool | None = None
    amc_api_key: str | None = None
    amc_vendor_id: str | None = None
    amc_movie_slug: str | None = None
    purchase_theatre: str | None = None
    num_tickets: int | None = None
    evening_start_hour: int | None = None
    report_seats: bool | None = None
    exclude_accessible_seats: bool | None = None
    require_standard_seats: bool | None = None
    use_browser: bool | None = None
    theatres: list[dict] | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/status")
async def status():
    """Current bot status: running/idle, schedule info, last run summary."""
    conf = cfg.get_config()
    runs = db.get_runs(limit=1)
    last_run = runs[0] if runs else None

    job = scheduler.get_job("ticket_check")
    next_run = job.next_run_time.isoformat() if job and job.next_run_time else None

    total_findings = len(db.get_findings(limit=1000))
    date_range = cfg.get_date_range()

    return {
        "is_checking": _is_running,
        "scheduler_running": scheduler.running,
        "poll_interval_minutes": int(conf.get("poll_interval_minutes", 1)),
        "next_check": next_run,
        "last_run": last_run,
        "movie_name": conf.get("movie_name", ""),
        "movie_year": conf.get("movie_year", ""),
        "movie_format": conf.get("movie_format", ""),
        "target_date": conf.get("target_date", ""),
        "start_date": conf.get("start_date", ""),
        "end_date": conf.get("end_date", ""),
        "dates_tracked": len(date_range),
        "total_findings": total_findings,
        "total_runs": len(db.get_runs(limit=10000)),
    }


@app.get("/api/runs")
async def get_runs(limit: int = 50, offset: int = 0):
    return db.get_runs(limit=limit, offset=offset)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: int):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@app.get("/api/findings")
async def get_findings(limit: int = 100):
    return db.get_findings(limit=limit)


@app.get("/api/config")
async def get_config():
    conf = cfg.get_config()
    # Parse theatres from JSON string if needed
    theatres = conf.get("theatres", "[]")
    if isinstance(theatres, str):
        try:
            theatres = json.loads(theatres)
        except json.JSONDecodeError:
            theatres = []
    conf["theatres"] = theatres
    # Parse booleans
    for bool_key in ["notifications_enabled", "report_seats", "exclude_accessible_seats", "require_standard_seats", "use_browser"]:
        val = conf.get(bool_key)
        if isinstance(val, str):
            conf[bool_key] = val.lower() in ("true", "1", "yes")
    return conf


@app.put("/api/config")
async def update_config(update: ConfigUpdate):
    updates = {k: v for k, v in update.model_dump().items() if v is not None}

    # Serialise complex types
    if "theatres" in updates:
        updates["theatres"] = json.dumps(updates["theatres"])
    for bool_key in ("notifications_enabled", "report_seats", "exclude_accessible_seats", "require_standard_seats", "use_browser"):
        if bool_key in updates:
            updates[bool_key] = str(updates[bool_key]).lower()

    cfg.update_config(updates)

    # Reschedule if interval changed
    if "poll_interval_minutes" in updates:
        _reschedule(int(updates["poll_interval_minutes"]))

    return {"status": "ok", "config": cfg.get_config()}


@app.post("/api/trigger")
async def trigger_check():
    """Trigger an immediate ad-hoc check."""
    if _is_running:
        return JSONResponse(
            status_code=409,
            content={"status": "already_running", "message": "A check is already in progress."},
        )

    asyncio.create_task(scheduled_check())
    return {"status": "triggered", "message": "Ad-hoc check started."}


@app.delete("/api/history")
async def clear_history():
    """Delete all bot runs and ticket findings."""
    db.clear_history()
    return {"status": "ok", "message": "All runs and findings cleared."}


@app.post("/api/test-notification")
async def test_notification():
    """Send a test notification to verify ntfy.sh is working."""
    await notifier.send_status_update(
        "🔔 Test notification from Odyssey IMAX 70mm Ticket Hunter!\nIf you see this, notifications are working.",
        priority="high",
    )
    return {"status": "sent", "message": "Test notification sent. Check your ntfy app!"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    reload = os.environ.get("RAILWAY_ENVIRONMENT") is None  # only reload locally

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
    )
