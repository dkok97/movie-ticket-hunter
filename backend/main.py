"""Dhurandhar Ticket Hunter — FastAPI backend.

Periodically checks AMC and Fandango for movie ticket availability
and sends push notifications via ntfy.sh when tickets drop.
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
log = logging.getLogger("dhurandhar.main")

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

scheduler = AsyncIOScheduler()
_is_running = False  # guard to prevent concurrent runs


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
        findings, sources, details = await checker.run_all_checks()

        db.complete_run(run_id, sources, len(findings), details)

        if findings:
            db.save_findings(run_id, findings)
            log.info("🎟️  Found %d ticket result(s)! Sending notification...", len(findings))
            await notifier.send_ticket_alert(findings)
        else:
            log.info("No tickets found this cycle.")

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
    log.info("🎬 Dhurandhar Ticket Hunter started!")

    yield

    scheduler.shutdown(wait=False)
    log.info("Scheduler shut down.")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Dhurandhar Ticket Hunter",
    description="Movie ticket availability bot",
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
    target_date: str | None = None
    poll_interval_minutes: int | None = None
    ntfy_topic: str | None = None
    ntfy_server: str | None = None
    notifications_enabled: bool | None = None
    amc_api_key: str | None = None
    amc_vendor_id: str | None = None
    amc_movie_slug: str | None = None
    evening_start_hour: int | None = None
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

    return {
        "is_checking": _is_running,
        "scheduler_running": scheduler.running,
        "poll_interval_minutes": int(conf.get("poll_interval_minutes", 1)),
        "next_check": next_run,
        "last_run": last_run,
        "movie_name": conf.get("movie_name", ""),
        "movie_year": conf.get("movie_year", ""),
        "target_date": conf.get("target_date", ""),
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
    for bool_key in ["notifications_enabled"]:
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
    if "notifications_enabled" in updates:
        updates["notifications_enabled"] = str(updates["notifications_enabled"]).lower()

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
        "🔔 Test notification from Dhurandhar Ticket Hunter!\nIf you see this, notifications are working.",
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
