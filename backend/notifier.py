"""Push notifications via ntfy.sh — completely free, no signup needed.

How to subscribe:
  1. Install the ntfy app (iOS / Android) or visit https://ntfy.sh in a browser
  2. Subscribe to the topic configured below (default: "odyssey-imax-70mm")
  3. That's it — you'll get instant push notifications when open seats drop!

Purchase mode:
  When open seats are found at the configured purchase_theatre (default:
  AMC Lincoln Square 13), the notification includes a 1-click deep link to
  buy tickets directly on AMC.
"""

import httpx
import logging
from datetime import date

import config as cfg

log = logging.getLogger("odyssey.notifier")


def _format_when(show_date: str, showtime: str) -> str:
    """Human-friendly 'Mon Aug 03, 2026 · 2:00 AM' from ISO date + time text."""
    nice_date = show_date
    try:
        nice_date = date.fromisoformat(show_date).strftime("%a %b %d, %Y")
    except (ValueError, TypeError):
        pass
    showtime = (showtime or "").strip()
    if nice_date and showtime:
        return f"{nice_date} · {showtime}"
    return nice_date or showtime


def _build_amc_purchase_url(finding: dict) -> str:
    """Build the best possible AMC deep link for purchasing tickets.

    AMC URL hierarchy (most specific → least):
      1. ticket_url from the finding (if it's a direct order/checkout link)
      2. /movies/{slug}/showtimes/{theatre-slug}/{date}  (theatre-specific movie page)
      3. /movies/{slug}/showtimes?date={date}             (generic movie page)
      4. ticket_url as-is (fallback)
    """
    conf = cfg.get_config()
    movie_slug = conf.get("amc_movie_slug", "")
    # Prefer the finding's own date (we sweep a range of dates).
    target_date = finding.get("show_date", "") or conf.get("target_date", "")
    ticket_url = finding.get("ticket_url", "")

    # If we already have a direct purchase/seat-selection link, use it
    if ticket_url and any(kw in ticket_url.lower() for kw in ("/seats", "/order", "/checkout", "ticketing")):
        return ticket_url

    # Build theatre-specific movie page URL
    theatres = cfg.get_theatres()
    purchase_theatre = conf.get("purchase_theatre", "AMC Lincoln Square 13")
    theatre_slug = ""
    for t in theatres:
        if t.get("name", "").lower() == purchase_theatre.lower():
            theatre_slug = t.get("slug", "")
            break

    if movie_slug and theatre_slug and target_date:
        return f"https://www.amctheatres.com/movies/{movie_slug}/showtimes/{theatre_slug}/{target_date}"

    if movie_slug and target_date:
        return f"https://www.amctheatres.com/movies/{movie_slug}/showtimes?date={target_date}"

    return ticket_url or "https://www.amctheatres.com"


async def send_ticket_alert(findings: list[dict]):
    """Send purchase-focused push notifications when tickets are found.

    If findings include the purchase_theatre, sends a special high-priority
    notification with 1-click buy links. All other findings get a standard alert.
    """
    conf = cfg.get_config()
    if not conf.get("notifications_enabled") or conf.get("notifications_enabled") == "false":
        log.info("Notifications disabled, skipping alert.")
        return

    topic = conf.get("ntfy_topic", "odyssey-imax-70mm")
    server = conf.get("ntfy_server", "https://ntfy.sh")
    movie_name = conf.get("movie_name", "Unknown Movie")
    movie_format = conf.get("movie_format", "")
    movie = f"{movie_name} ({movie_format})" if movie_format else movie_name
    purchase_theatre = conf.get("purchase_theatre", "AMC Lincoln Square 13")
    num_tickets = int(conf.get("num_tickets", 2))

    # Split findings into purchase-theatre vs others
    purchase_findings = []
    other_findings = []
    for f in findings:
        if f.get("theatre_name", "").lower() == purchase_theatre.lower():
            purchase_findings.append(f)
        else:
            other_findings.append(f)

    # --- Send purchase-theatre alert (high priority with buy links) ---
    if purchase_findings:
        await _send_purchase_alert(
            server, topic, movie,
            purchase_theatre, num_tickets, purchase_findings,
        )

    # --- Send general alert for other theatres ---
    if other_findings:
        await _send_general_alert(server, topic, movie, other_findings)


async def _send_purchase_alert(
    server: str, topic: str, movie: str,
    purchase_theatre: str, num_tickets: int, findings: list[dict],
):
    """Send a purchase-ready notification for the priority theatre."""
    best_url = _build_amc_purchase_url(findings[0])

    # Build message body
    lines = [
        f"OPEN SEATS at {purchase_theatre}!",
        f"Movie: {movie}",
        f"Tickets needed: {num_tickets}",
        "",
    ]

    for i, f in enumerate(findings, 1):
        showtime = f.get("showtime", "Unknown time")
        show_date = f.get("show_date", "")
        url = f.get("ticket_url", best_url)
        when = _format_when(show_date, showtime)
        status = f.get("status", "")
        status_str = f" — {status}" if status else ""
        lines.append(f"Showtime {i}: {when}{status_str}")
        seat_summary = f.get("seat_summary", "")
        if seat_summary:
            lines.append(f"  Seats: {seat_summary}")
        lines.append(f"  Link: {url}")
        lines.append("")

    lines.append("TAP 'BUY NOW' TO GRAB SEATS IMMEDIATELY")

    message = "\n".join(lines)

    # ntfy actions: up to 3 action buttons
    # First action = primary buy link
    actions = [f"view, BUY NOW - {purchase_theatre}, {best_url}"]

    # Add individual showtime links if there are multiple
    for i, f in enumerate(findings[:2], 1):
        url = f.get("ticket_url", best_url)
        showtime = f.get("showtime", f"Show {i}")
        if url and url != best_url:
            actions.append(f"view, {showtime}, {url}")

    headers = {
        "Title": f"BUY TICKETS: {movie} at {purchase_theatre}",
        "Priority": "max",
        "Tags": "rotating_light,ticket,movie_camera",
        "Click": best_url,
        "Actions": "; ".join(actions),
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{server}/{topic}",
                content=message.encode("utf-8"),
                headers=headers,
            )
            resp.raise_for_status()
            log.info("PURCHASE ALERT sent to %s/%s — %s", server, topic, purchase_theatre)
    except Exception as exc:
        log.error("Failed to send purchase alert: %s", exc)


async def _send_general_alert(
    server: str, topic: str, movie: str, findings: list[dict],
):
    """Send a standard notification for non-purchase theatres."""
    lines = [f"Open seats found for {movie}!\n"]
    ticket_url = ""
    for f in findings:
        lines.append(f"  {f.get('theatre_name', 'Unknown Theatre')}")
        when = _format_when(f.get("show_date", ""), f.get("showtime", ""))
        status = f.get("status", "")
        if when:
            lines.append(f"    {when}{f'  —  {status}' if status else ''}")
        if f.get("seat_summary"):
            lines.append(f"    Seats: {f['seat_summary']}")
        if f.get("ticket_url"):
            lines.append(f"    {f['ticket_url']}")
            if not ticket_url:
                ticket_url = f["ticket_url"]
        lines.append("")

    message = "\n".join(lines)

    headers = {
        "Title": f"{movie} — Tickets Available!",
        "Priority": "urgent",
        "Tags": "movie_camera,ticket",
    }
    if ticket_url:
        headers["Click"] = ticket_url
        headers["Actions"] = f"view, View Tickets, {ticket_url}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{server}/{topic}",
                content=message.encode("utf-8"),
                headers=headers,
            )
            resp.raise_for_status()
            log.info("General alert sent to %s/%s", server, topic)
    except Exception as exc:
        log.error("Failed to send general alert: %s", exc)


async def send_status_update(message: str, priority: str = "default"):
    """Send a generic status update notification."""
    conf = cfg.get_config()
    if not conf.get("notifications_enabled") or conf.get("notifications_enabled") == "false":
        return

    topic = conf.get("ntfy_topic", "odyssey-imax-70mm")
    server = conf.get("ntfy_server", "https://ntfy.sh")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"{server}/{topic}",
                content=message.encode("utf-8"),
                headers={
                    "Title": "Odyssey IMAX 70mm Ticket Hunter",
                    "Priority": priority,
                    "Tags": "mag",
                },
            )
    except Exception as exc:
        log.error("Failed to send status update: %s", exc)
