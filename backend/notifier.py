"""Push notifications via ntfy.sh — completely free, no signup needed.

How to subscribe:
  1. Install the ntfy app (iOS / Android) or visit https://ntfy.sh in a browser
  2. Subscribe to the topic configured below (default: "dhurandhar-tickets")
  3. That's it — you'll get instant push notifications when tickets drop!
"""

import httpx
import logging

import config as cfg

log = logging.getLogger("dhurandhar.notifier")


async def send_ticket_alert(findings: list[dict]):
    """Send a push notification when tickets are found."""
    conf = cfg.get_config()
    if not conf.get("notifications_enabled") or conf.get("notifications_enabled") == "false":
        log.info("Notifications disabled, skipping alert.")
        return

    topic = conf.get("ntfy_topic", "dhurandhar-tickets")
    server = conf.get("ntfy_server", "https://ntfy.sh")
    movie = conf.get("movie_name", "Unknown Movie")

    lines = [f"🎬 Tickets found for {movie}!\n"]
    ticket_url = ""
    for f in findings:
        lines.append(f"🎟️  {f.get('theatre_name', 'Unknown Theatre')}")
        if f.get("showtime"):
            lines.append(f"    🕐 {f['showtime']}")
        if f.get("ticket_url"):
            lines.append(f"    🔗 {f['ticket_url']}")
            if not ticket_url:
                ticket_url = f["ticket_url"]
        lines.append("")

    message = "\n".join(lines)

    headers = {
        "Title": f"🎬 {movie} — Tickets Available!",
        "Priority": "urgent",
        "Tags": "movie_camera,ticket,tada",
    }
    if ticket_url:
        headers["Click"] = ticket_url
        headers["Actions"] = f"view, Buy Tickets, {ticket_url}"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{server}/{topic}",
                content=message.encode("utf-8"),
                headers=headers,
            )
            resp.raise_for_status()
            log.info("Notification sent to %s/%s", server, topic)
    except Exception as exc:
        log.error("Failed to send notification: %s", exc)


async def send_status_update(message: str, priority: str = "default"):
    """Send a generic status update notification."""
    conf = cfg.get_config()
    if not conf.get("notifications_enabled") or conf.get("notifications_enabled") == "false":
        return

    topic = conf.get("ntfy_topic", "dhurandhar-tickets")
    server = conf.get("ntfy_server", "https://ntfy.sh")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(
                f"{server}/{topic}",
                content=message.encode("utf-8"),
                headers={
                    "Title": "Dhurandhar Ticket Hunter",
                    "Priority": priority,
                    "Tags": "mag",
                },
            )
    except Exception as exc:
        log.error("Failed to send status update: %s", exc)
