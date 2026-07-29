"""Odyssey IMAX 70mm Ticket Hunter — FastAPI backend.

Periodically checks AMC (and Fandango) for open seats to Christopher Nolan's
"The Odyssey" in IMAX 70mm at AMC Lincoln Square, sweeping a range of dates,
and sends push notifications via ntfy.sh the moment seats open up.
"""

import asyncio
import json
import logging
import re
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

# Signatures of findings we've already alerted on. Includes the open-seat
# set so a showtime that gains *different* seats later re-alerts. If a
# showtime sells out entirely, its sig drops out of this set and we re-alert
# when seats open again.
_notified_signatures: set[str] = set()

# How many guest-checkout holds we've attempted in the current cycle
# (reset at the start of each scheduled_check). Caps proxy burn.
_holds_this_cycle: int = 0
_hold_claim_lock = asyncio.Lock()
# Background hold+notify tasks spawned the instant a seat opens; awaited
# at end of cycle so we don't exit mid-hold.
_immediate_tasks: list[asyncio.Task] = []


def _finding_signature(f: dict) -> str:
    """Stable key: date + time + theatre + open seats.

    Seat names are sorted so map order doesn't cause spurious re-alerts; a
    *changed* seat set (new cancellation) does re-alert.
    """
    seats = f.get("available_seats") or []
    seat_key = ",".join(sorted(str(s) for s in seats))
    return "|".join([
        str(f.get("show_date", "")),
        str(f.get("showtime", "")),
        str(f.get("theatre_name", "")),
        seat_key,
    ])


def _showtime_prefix(f: dict) -> str:
    """date|time|theatre — identity of a showtime ignoring which seats are open."""
    return "|".join([
        str(f.get("show_date", "")),
        str(f.get("showtime", "")),
        str(f.get("theatre_name", "")),
    ])


def _showtime_sort_key(showtime: str) -> int:
    """Minutes from midnight for strings like '2:00pm' / '10:00 am' / '14:00'."""
    s = (showtime or "").strip().lower().replace(" ", "")
    m = re.match(r"^(\d{1,2}):(\d{2})(am|pm)?$", s)
    if not m:
        return 99_999
    hour, minute = int(m.group(1)), int(m.group(2))
    ampm = m.group(3)
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    return hour * 60 + minute


def _rank_findings(findings: list[dict]) -> list[dict]:
    """Soonest show_date first, then earliest showtime — best hold targets."""
    return sorted(
        findings,
        key=lambda f: (
            str(f.get("show_date") or "9999-99-99"),
            _showtime_sort_key(str(f.get("showtime") or "")),
        ),
    )


async def _on_open_seat(finding: dict, *, run_id: int) -> None:
    """Called the instant a seat map confirms open seats.

    Must return quickly so other showtimes keep verifying. Spawns a background
    task that:
      1. Notifies immediately with the seat-map link (no waiting on hold)
      2. Attempts a guest-checkout hold in parallel with you opening the app
      3. If a hold lands, sends a second "SEAT HELD" ntfy with the order URL

    Confirmed via production (2026-07-29): awaiting the hold *before* ntfy
    added ~27s of lag — by then F20/F19 on an Odyssey show were already gone.
    """
    global _holds_this_cycle

    sig = _finding_signature(finding)
    async with _hold_claim_lock:
        if sig in _notified_signatures:
            return
        _notified_signatures.add(sig)
        try:
            max_holds = max(0, int(cfg.get("max_holds_per_cycle", 2) or 2))
        except (TypeError, ValueError):
            max_holds = 2
        should_hold = _holds_this_cycle < max_holds
        if should_hold:
            _holds_this_cycle += 1

    log.info(
        "🎟️  Open seats NOW %s %s @ %s (%s) — notify now%s",
        finding.get("show_date"),
        finding.get("showtime"),
        finding.get("theatre_name"),
        finding.get("seat_summary", ""),
        ", hold in background" if should_hold else " (hold cap reached)",
    )

    async def _notify_then_hold():
        try:
            db.save_findings(run_id, [finding])
            # 1) Alert immediately — seats link. Do not wait on Playwright.
            await notifier.send_ticket_alert([finding])
            if not should_hold:
                return
            # 2) Hold in the background; 3) follow-up ntfy if we got an order.
            await _hold_seats_for_findings([finding])
            if finding.get("order_url"):
                log.info(
                    "🎟️  Hold succeeded for %s — sending SEAT HELD follow-up",
                    finding.get("showtime_id"),
                )
                await notifier.send_ticket_alert([finding])
        except Exception as exc:
            log.exception(
                "Immediate notify/hold failed for %s: %s",
                finding.get("showtime_id"), exc,
            )

    _immediate_tasks.append(asyncio.create_task(_notify_then_hold()))


async def _process_new_findings(findings: list[dict], *, label: str = "") -> int:
    """Notify (and hold if capacity remains) for findings not already handled
    by the immediate on_open_seat path. Returns newly alerted count.

    Same notify-first / hold-second order as ``_on_open_seat`` so leftovers
    don't inherit the old "wait for hold before ntfy" lag.
    """
    global _holds_this_cycle

    if not findings:
        return 0

    new_findings = [
        f for f in findings if _finding_signature(f) not in _notified_signatures
    ]
    if not new_findings:
        return 0

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
                "Skipping notify for %d unverified finding(s)%s",
                skipped, f" ({label})" if label else "",
            )
        new_findings = verified

    if not new_findings:
        return 0

    try:
        max_holds = max(0, int(cfg.get("max_holds_per_cycle", 2) or 2))
    except (TypeError, ValueError):
        max_holds = 2
    remaining_holds = max(0, max_holds - _holds_this_cycle)
    to_hold = _rank_findings(new_findings)[:remaining_holds]

    _notified_signatures.update(_finding_signature(f) for f in new_findings)
    log.info(
        "🎟️  %d leftover open showtime(s)%s — notify now, holding %d...",
        len(new_findings),
        f" [{label}]" if label else "",
        len(to_hold),
    )
    await notifier.send_ticket_alert(new_findings)
    if to_hold:
        await _hold_seats_for_findings(to_hold)
        _holds_this_cycle += len(to_hold)
        held = [f for f in to_hold if f.get("order_url")]
        if held:
            await notifier.send_ticket_alert(held)
    return len(new_findings)


async def scheduled_check():
    """Runs as a scheduled job — perform a full ticket check cycle."""
    global _is_running, _holds_this_cycle, _notified_signatures, _immediate_tasks
    if _is_running:
        log.info("Check already in progress, skipping this cycle.")
        return

    _is_running = True
    _holds_this_cycle = 0
    _immediate_tasks = []
    run_id = db.create_run()

    try:
        log.info("=== Starting scheduled check (run #%d) ===", run_id)

        async def on_open_seat(finding: dict):
            await _on_open_seat(finding, run_id=run_id)

        # Watchdog: never let a single cycle hang forever (e.g. a stalled
        # request), which would otherwise block all future cycles.
        findings, sources, details = await asyncio.wait_for(
            checker.run_all_checks(on_open_seat=on_open_seat),
            timeout=CHECK_TIMEOUT_SECONDS,
        )

        # Let any in-flight immediate holds finish before we finalize the run.
        if _immediate_tasks:
            await asyncio.gather(*_immediate_tasks, return_exceptions=True)
            _immediate_tasks = []

        db.complete_run(run_id, sources, len(findings), details)

        if findings:
            db.save_findings(run_id, findings)
            # Drop signatures for showtimes no longer open so a full sell-out
            # followed by a new cancellation re-alerts.
            live_prefixes = {_showtime_prefix(f) for f in findings}
            _notified_signatures = {
                sig for sig in _notified_signatures
                if "|".join(sig.split("|")[:3]) in live_prefixes
            }

            n = await _process_new_findings(findings, label="full")
            if n == 0:
                log.info(
                    "%d open showtime(s), all previously alerted or unverified — no new notification.",
                    len(findings),
                )
        else:
            _notified_signatures.clear()
            log.info("No open seats found this cycle.")

    except Exception as exc:
        log.exception("Check failed: %s", exc)
        db.fail_run(run_id, str(exc))
        if _immediate_tasks:
            await asyncio.gather(*_immediate_tasks, return_exceptions=True)
            _immediate_tasks = []
    finally:
        db.gc_runs(keep=100)
        _is_running = False


async def _hold_seats_for_findings(findings: list[dict]):
    """Best-effort: for each finding, drive AMC guest checkout far enough to
    mint a real order URL (holding the seat). Never raises.
    """
    if str(cfg.get("auto_hold_seats", True)).lower() in ("false", "0", "no"):
        return
    if not findings:
        return

    import purchase

    try:
        num_seats = max(1, int(cfg.get("num_tickets", 1) or 1))
    except (TypeError, ValueError):
        num_seats = 1

    sem = asyncio.Semaphore(2)

    async def _hold(f: dict):
        showtime_id = f.get("showtime_id", "")
        if not showtime_id:
            return
        async with sem:
            try:
                diag = await asyncio.wait_for(
                    purchase.hold_seat_verbose(
                        showtime_id,
                        seat_names=f.get("available_seats", []),
                        num_seats=num_seats,
                    ),
                    timeout=75,
                )
            except Exception as exc:
                log.warning("Seat hold errored for showtime %s: %s", showtime_id, exc)
                return
            if diag.get("order_url"):
                f["order_url"] = diag["order_url"]
            else:
                log.warning(
                    "No hold for showtime %s: stage=%s error=%s final_url=%s final_title=%s",
                    showtime_id, diag.get("stage"), diag.get("error"),
                    diag.get("final_url"), diag.get("final_title"),
                )

    await asyncio.gather(*[_hold(f) for f in findings])


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
    # Pre-warm Chromium immediately; sticky-proxy hold context in the
    # background so Railway's healthcheck (often ~10s) isn't blocked by a
    # slow IPRoyal + AMC priming navigation.
    try:
        import browser
        import purchase
        await browser._get_browser()
        asyncio.create_task(purchase.ensure_warm_hold_context())
    except Exception as exc:
        log.warning("Hold pre-warm failed (holds will retry on demand): %s", exc)
    log.info("🎬 Odyssey IMAX 70mm Ticket Hunter started!")

    yield

    scheduler.shutdown(wait=False)
    try:
        import purchase
        await purchase.shutdown_warm_hold_context()
    except Exception:
        pass
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
    auto_hold_seats: bool | None = None
    priority_days: int | None = None
    max_holds_per_cycle: int | None = None
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
    for bool_key in ["notifications_enabled", "report_seats", "exclude_accessible_seats", "require_standard_seats", "use_browser", "auto_hold_seats"]:
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
    for bool_key in ("notifications_enabled", "report_seats", "exclude_accessible_seats", "require_standard_seats", "use_browser", "auto_hold_seats"):
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


@app.get("/api/debug/hold-test")
async def debug_hold_test(showtime_id: str = "", ticket_url: str = "", num_seats: int = 1):
    """Manually run the seat-hold flow against a real showtime and return
    full diagnostics — for verifying auto_hold_seats actually works from
    this deployment (AMC's checkout/order-creation step may be guarded by
    stricter anti-fraud rules than plain page views, which can behave very
    differently from a datacenter IP like Railway's vs. a residential one).

    Pass either `showtime_id` directly, or `ticket_url` (the seat-map link
    from a notification / /api/findings, e.g.
    https://www.amctheatres.com/showtimes/145301196/seats) and the id will
    be extracted for you.
    """
    import re as _re
    import purchase

    if not showtime_id and ticket_url:
        m = _re.search(r"/showtimes/(\d+)", ticket_url)
        showtime_id = m.group(1) if m else ""

    if not showtime_id:
        raise HTTPException(400, "Provide showtime_id or a ticket_url containing /showtimes/{id}")

    try:
        result = await asyncio.wait_for(
            purchase.hold_seat_verbose(showtime_id, num_seats=num_seats, debug_screenshot=True),
            timeout=180,
        )
    except asyncio.TimeoutError:
        return {"order_url": None, "stage": "timeout", "error": "hold_seat_verbose exceeded 180s"}
    return result


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
