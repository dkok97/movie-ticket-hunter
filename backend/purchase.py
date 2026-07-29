"""Best-effort AMC seat *hold* via guest checkout — no login, no payment.

AMC lets you select seats and a ticket type, and create a real order, entirely
as a guest (no AMC Stubs account, no saved card). That order holds the
seat(s) for several minutes while you complete payment. This module drives
that flow (seats -> tickets -> order) far enough to mint a shareable order URL
(https://www.amctheatres.com/orders/{uuid}) that a human can open on their own
phone/browser to finish checkout — closing the gap between "a seat just
opened up" and "you have an actual held ticket", without the bot ever
touching payment info.

Confirmed by hand: opening the resulting order URL from a normal (non-bot)
browser loads the real order and lets you pay.

Important gotcha: AMC's checkout is behind Cloudflare bot-detection that can
flag *our own automated session* if we keep hammering it right after the
order is created (e.g. reloading the order page to double check it worked).
So this module captures the order URL and closes the browser context
immediately — it never re-visits the order itself.

Confirmed via production debugging (2026-07-29): AMC's CDN returns HTTP 403
for every JS/CSS asset request (`/_next/static/...`) when the browser runs
from Railway's datacenter IP, even though the top-level seat-map document
itself loads fine (200, via the Queue-it redirect). Without its JS bundle,
the page has zero interactivity — that's the real cause of every "click
didn't work" failure mode seen in this file's history (broken zoom-scale
seat map, a ~150,000px-tall ticket screen, clicks that report success but
never change app state). This never reproduced from a residential/dev IP.
So: the browser context used for the *hold* flow specifically (not the much
higher-volume plain scraping/seat-checking path, which never needs JS to
run) is routed through a residential/mobile proxy when one is configured, via
the HOLD_PROXY_SERVER / HOLD_PROXY_USERNAME / HOLD_PROXY_PASSWORD env vars
(see README). Without a proxy configured, this still runs — from a
datacenter host it just won't be able to complete a hold, same as before.
"""

import base64
import logging
import os
import re

import browser as br

log = logging.getLogger("odyssey.purchase")

_ORDER_URL_RE = re.compile(r"https://www\.amctheatres\.com/orders/[0-9a-f-]+", re.IGNORECASE)

# Same accessible/wheelchair-seat detector checker.py uses to keep these out
# of notifications. Findings passed in from the real auto-hold path already
# only contain standard seat names, but the debug endpoint can be called with
# just a showtime_id (no seat_names), so `_select_seats` needs its own guard:
# a lone wheelchair seat's checkbox is clickable, but AMC's checkout disables
# 'Continue' for it (it requires a paired companion seat), which otherwise
# looks just like an unexplained stuck flow.
_ACCESSIBLE_SEAT_RE = re.compile(
    r"wheel\s*chair|companion|accessible|handicap|\bada\b", re.IGNORECASE
)

# Confirmed via a live debug dump that AMC's real buttons are
# "Close this consent banner" / "Close Cookie Preferences" — neither matched
# the exact-text/exact-aria-label selectors this used to have, meaning the
# consent banner was silently never getting dismissed. Use a substring match
# instead so wording changes don't silently break this again.
_CLOSE_BANNER_SELECTORS = (
    'button[aria-label*="Close" i]',
    'button:has-text("Close")',
)


def _proxy_config() -> dict | None:
    """Playwright `proxy` dict for the hold flow's browser context, from
    HOLD_PROXY_SERVER (e.g. `http://geo.iproyal.com:12321`) plus optional
    HOLD_PROXY_USERNAME / HOLD_PROXY_PASSWORD. None if unset (falls back to
    the deployment's own IP, which works fine locally but not from a
    datacenter host like Railway — see module docstring)."""
    server = os.environ.get("HOLD_PROXY_SERVER", "").strip()
    if not server:
        return None
    cfg = {"server": server}
    username = os.environ.get("HOLD_PROXY_USERNAME", "").strip()
    password = os.environ.get("HOLD_PROXY_PASSWORD", "").strip()
    if username:
        cfg["username"] = username
    if password:
        cfg["password"] = password
    return cfg


async def hold_seat(
    showtime_id: str,
    seat_names: list[str] | None = None,
    num_seats: int = 1,
) -> str | None:
    """Thin wrapper around `hold_seat_verbose` for callers that only care
    about the resulting order URL (or None on any failure)."""
    result = await hold_seat_verbose(showtime_id, seat_names=seat_names, num_seats=num_seats)
    return result["order_url"]


async def hold_seat_verbose(
    showtime_id: str,
    seat_names: list[str] | None = None,
    num_seats: int = 1,
    debug_screenshot: bool = False,
) -> dict:
    """Select up to `num_seats` open seats (preferring `seat_names`, in
    order, when they're still open) and create a guest AMC order.

    Returns a diagnostics dict:
      {
        "order_url": str | None,   # the payoff, if we got one
        "stage": str,              # last stage reached (see STAGES below)
        "error": str | None,       # exception message, if any
        "final_url": str | None,   # whatever URL the browser ended up on
        "final_title": str | None, # that page's <title>, if we could read it
        "seats_selected": int,     # how many seat checkboxes we actually clicked
        "screenshot_b64": str | None,  # only set if debug_screenshot=True and it failed
        "using_proxy": bool,       # whether HOLD_PROXY_SERVER was configured for this run
      }

    This never raises — callers that only want the URL should use
    `hold_seat` instead. `debug_screenshot` adds real overhead (an extra
    page.screenshot() call) so it defaults off for the normal auto-hold path
    and is only meant for the manual /api/debug/hold-test endpoint.
    """
    proxy = _proxy_config()
    diag = {
        "order_url": None, "stage": "start", "error": None,
        "final_url": None, "final_title": None, "seats_selected": 0,
        "screenshot_b64": None, "button_labels": None,
        "using_proxy": proxy is not None,
    }

    if not showtime_id:
        diag["error"] = "no showtime_id given"
        return diag
    num_seats = max(1, num_seats)
    seat_names = seat_names or []

    try:
        browser_ = await br._get_browser()
    except Exception as exc:
        diag["stage"] = "launch_browser"
        diag["error"] = f"Chromium unavailable: {exc}"
        log.error("Hold[%s]: Chromium unavailable (%s)", showtime_id, exc)
        return diag

    seats_url = f"https://www.amctheatres.com/showtimes/{showtime_id}/seats"
    context = None
    page = None
    console_errors: list[str] = []
    failed_requests: list[str] = []
    try:
        async with br._page_sem:
            context_kwargs = dict(
                user_agent=br.BROWSER_UA,
                viewport={"width": 1400, "height": 1100},
                locale="en-US",
            )
            if proxy:
                context_kwargs["proxy"] = proxy
            try:
                context = await browser_.new_context(**context_kwargs)
            except Exception as exc:
                diag["stage"] = "launch_browser"
                diag["error"] = f"Failed to open browser context (bad proxy config?): {exc}"
                log.error("Hold[%s]: %s", showtime_id, diag["error"])
                return diag
            page = await context.new_page()
            if debug_screenshot:
                # Only wired up for the manual debug endpoint — these listeners add
                # overhead we don't want on the hot auto-hold path.
                page.on("pageerror", lambda exc: console_errors.append(f"pageerror: {exc}"))
                page.on(
                    "console",
                    lambda msg: console_errors.append(f"console.{msg.type}: {msg.text}")
                    if msg.type in ("error", "warning") else None,
                )
                page.on(
                    "requestfailed",
                    lambda req: failed_requests.append(f"{req.method} {req.url} — {req.failure}"),
                )

            diag["stage"] = "goto_seats_page"
            await page.goto(seats_url, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)
            await _dismiss_cookie_banner(page)

            diag["stage"] = "select_seats"
            selected = await _select_seats(page, seat_names, num_seats, diag)
            diag["seats_selected"] = selected
            if selected == 0:
                diag["error"] = "no open/selectable seat checkboxes found on the live page"
                log.warning("Hold[%s]: %s (sold out, or page shape changed)", showtime_id, diag["error"])
                return await _finish(diag, page, debug_screenshot, console_errors, failed_requests)

            diag["stage"] = "continue_to_tickets"
            if not await _click_continue(page):
                diag["error"] = "'Continue' button not found/clickable after selecting seat(s)"
                diag["button_labels"] = await _dump_button_labels(page)
                log.warning("Hold[%s]: %s", showtime_id, diag["error"])
                return await _finish(diag, page, debug_screenshot, console_errors, failed_requests)

            await page.wait_for_timeout(1500)

            diag["stage"] = "add_ticket_type"
            added = 0
            for _ in range(selected):
                if await _robust_click(page, 'button[aria-label="Add Adult Ticket"]'):
                    added += 1
                else:
                    break
            if added == 0:
                diag["error"] = "'Add Adult Ticket' button not found/clickable on ticket-type page"
                diag["button_labels"] = await _dump_button_labels(page)
                log.warning("Hold[%s]: %s (labels seen: %s)", showtime_id, diag["error"], diag["button_labels"])
                return await _finish(diag, page, debug_screenshot, console_errors, failed_requests)

            diag["stage"] = "continue_to_order"
            if not await _click_continue(page):
                diag["error"] = "'Continue' button not found/clickable after choosing ticket type"
                diag["button_labels"] = await _dump_button_labels(page)
                log.warning("Hold[%s]: %s", showtime_id, diag["error"])
                return await _finish(diag, page, debug_screenshot, console_errors, failed_requests)

            diag["stage"] = "wait_for_order"
            try:
                await page.wait_for_url(re.compile(r".*/orders/.*"), timeout=20000)
            except Exception:
                pass
            final_url = page.url

            if "/orders/" not in final_url:
                diag["error"] = "never reached an /orders/ URL after checkout (blocked/redirected?)"
                log.warning(
                    "Hold[%s]: %s — landed on %s", showtime_id, diag["error"], final_url,
                )
                return await _finish(diag, page, debug_screenshot, console_errors, failed_requests)

            m = _ORDER_URL_RE.match(final_url)
            order_url = m.group(0) if m else final_url
            diag["stage"] = "done"
            diag["order_url"] = order_url
            log.info("Held %d seat(s) for showtime %s -> %s", selected, showtime_id, order_url)
            return await _finish(diag, page, debug_screenshot=False)
    except Exception as exc:
        diag["error"] = f"{type(exc).__name__}: {exc}"
        log.error("Hold[%s]: failed at stage=%s: %s", showtime_id, diag["stage"], exc)
        return await _finish(diag, page, debug_screenshot, console_errors, failed_requests)
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass


async def _finish(
    diag: dict,
    page,
    debug_screenshot: bool = False,
    console_errors: list[str] | None = None,
    failed_requests: list[str] | None = None,
) -> dict:
    """Best-effort: capture where the browser ended up (and, on failure when
    requested, a screenshot + console/network errors) for diagnostics."""
    if debug_screenshot and diag.get("order_url") is None:
        if console_errors:
            diag["console_errors"] = console_errors[:30]
        if failed_requests:
            diag["failed_requests"] = failed_requests[:30]
    if page is not None:
        try:
            diag["final_url"] = page.url
        except Exception:
            pass
        try:
            diag["final_title"] = await page.title()
        except Exception:
            pass
        if debug_screenshot and diag.get("order_url") is None:
            try:
                await page.locator('input[type="checkbox"][aria-label]').first.scroll_into_view_if_needed(timeout=5000)
            except Exception:
                pass
            png = None
            try:
                png = await page.screenshot(full_page=True, timeout=15000)
            except Exception as exc:
                log.warning("Full-page debug screenshot failed (%s), retrying viewport-only", exc)
                try:
                    png = await page.screenshot(full_page=False)
                except Exception:
                    pass
            if png:
                diag["screenshot_b64"] = base64.b64encode(png).decode("ascii")
    return diag


async def _dismiss_cookie_banner(page):
    """Dismiss cookie/consent banners — there can be more than one (e.g. a
    consent banner AND a separate cookie-preferences prompt), so try every
    selector rather than stopping at the first successful click."""
    for sel in _CLOSE_BANNER_SELECTORS:
        try:
            await page.locator(sel).first.click(timeout=2000)
        except Exception:
            continue


async def _click_continue(page) -> bool:
    """Click the 'Continue' button — a real click first, falling back to a
    JS-dispatched click on the same element.

    Confirmed on a live deployment: the seat map can render at a wildly
    broken scale in some headless-Chromium/Docker environments (one seat
    icon filling the whole viewport) even though the underlying DOM/data is
    fine — likely a font or GPU/canvas rendering difference vs. a desktop
    browser. That pushes real UI like the 'Continue' bar out of Playwright's
    actionable (visible, in-viewport, unobstructed) area, so a normal click
    times out even though the button exists and works. We already click seat
    checkboxes the same way, so do it here too rather than depend on visual
    layout being correct.
    """
    return await _robust_click(page, "text=Continue")


async def _robust_click(page, selector: str, timeout: int = 8000) -> bool:
    """Try a real click first; fall back to a JS-dispatched click on the same
    element if that times out.

    Deliberately skips `scroll_into_view_if_needed()` in the fallback: a
    JS-level `.click()` doesn't need the element to be visible/in-viewport at
    all, and on pages with broken layout (seen in production: a ticket-type
    screen rendering ~150,000px tall) scrolling into view can itself be slow
    or pointless.
    """
    try:
        await page.click(selector, timeout=timeout)
        return True
    except Exception:
        pass
    try:
        loc = page.locator(selector).first
        if await loc.count() == 0:
            return False
        await loc.evaluate("el => el.click()")
        return True
    except Exception:
        return False


async def _dump_button_labels(page, limit: int = 40) -> list[str]:
    """Best-effort diagnostic: aria-labels of buttons currently in the DOM,
    for figuring out what a failed click *should* have targeted."""
    try:
        labels = await page.eval_on_selector_all(
            "button[aria-label]",
            "(els) => els.map(e => e.getAttribute('aria-label'))",
        )
        return labels[:limit]
    except Exception:
        return []


async def _select_seats(page, seat_names: list[str], num_seats: int, diag: dict | None = None) -> int:
    """Click up to `num_seats` open seat checkboxes on the live page,
    preferring seats named in `seat_names` (in order) when still open.

    Re-checks openness live rather than trusting `seat_names` blindly, since
    a little time may have passed since that list was gathered.
    """
    checkboxes = page.locator('input[type="checkbox"][aria-label]')
    count = await checkboxes.count()

    open_seats = []  # (index, seat_code)
    n_disabled = n_accessible = 0
    for i in range(count):
        cb = checkboxes.nth(i)
        try:
            if await cb.is_disabled():
                n_disabled += 1
                continue
            label = (await cb.get_attribute("aria-label")) or ""
        except Exception:
            continue
        if _ACCESSIBLE_SEAT_RE.search(label):
            n_accessible += 1
            continue
        code = label.strip().split(" ")[-1] if label else ""
        open_seats.append((i, code))
    log.info(
        "Seat map for hold: %d checkbox(es) total, %d disabled, %d accessible, %d open-standard",
        count, n_disabled, n_accessible, len(open_seats),
    )
    if diag is not None:
        diag["seat_checkbox_count"] = count
        diag["seat_disabled_count"] = n_disabled
        diag["seat_accessible_count"] = n_accessible

    priority = {code: idx for idx, code in enumerate(seat_names)}
    open_seats.sort(key=lambda pair: priority.get(pair[1], len(priority)))

    selected = 0
    for i, _code in open_seats:
        if selected >= num_seats:
            break
        try:
            cb = checkboxes.nth(i)
            await cb.scroll_into_view_if_needed()
            await cb.evaluate("el => el.click()")
            selected += 1
        except Exception:
            continue
    return selected
