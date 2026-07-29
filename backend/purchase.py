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

Note: this flow was only validated by hand from a residential/dev IP.
Checkout/order-creation endpoints are frequently guarded by stricter
anti-fraud rules than plain page views (datacenter IP ranges — which is what
a Railway deployment looks like to AMC — are a classic trigger), so
production behavior can differ from local testing. `hold_seat_verbose` exists
to make that failure mode diagnosable from the deployed environment without
needing full server log access.
"""

import logging
import re

import browser as br

log = logging.getLogger("odyssey.purchase")

_ORDER_URL_RE = re.compile(r"https://www\.amctheatres\.com/orders/[0-9a-f-]+", re.IGNORECASE)

_CLOSE_BANNER_SELECTORS = ('button[aria-label="Close"]', 'button:has-text("Close")')


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
      }

    This never raises — callers that only want the URL should use
    `hold_seat` instead.
    """
    diag = {"order_url": None, "stage": "start", "error": None, "final_url": None, "final_title": None}

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
    try:
        async with br._page_sem:
            context = await browser_.new_context(
                user_agent=br.BROWSER_UA,
                viewport={"width": 1400, "height": 1100},
                locale="en-US",
            )
            page = await context.new_page()

            diag["stage"] = "goto_seats_page"
            await page.goto(seats_url, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)
            await _dismiss_cookie_banner(page)

            diag["stage"] = "select_seats"
            selected = await _select_seats(page, seat_names, num_seats)
            if selected == 0:
                diag["error"] = "no open/selectable seat checkboxes found on the live page"
                log.warning("Hold[%s]: %s (sold out, or page shape changed)", showtime_id, diag["error"])
                return await _finish(diag, page)

            diag["stage"] = "continue_to_tickets"
            if not await _click_continue(page):
                diag["error"] = "'Continue' button not found/clickable after selecting seat(s)"
                log.warning("Hold[%s]: %s", showtime_id, diag["error"])
                return await _finish(diag, page)

            await page.wait_for_timeout(1500)

            diag["stage"] = "add_ticket_type"
            added = 0
            for _ in range(selected):
                try:
                    await page.click('button[aria-label="Add Adult Ticket"]', timeout=5000)
                    added += 1
                except Exception:
                    break
            if added == 0:
                diag["error"] = "'Add Adult Ticket' button not found/clickable on ticket-type page"
                log.warning("Hold[%s]: %s", showtime_id, diag["error"])
                return await _finish(diag, page)

            diag["stage"] = "continue_to_order"
            if not await _click_continue(page):
                diag["error"] = "'Continue' button not found/clickable after choosing ticket type"
                log.warning("Hold[%s]: %s", showtime_id, diag["error"])
                return await _finish(diag, page)

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
                return await _finish(diag, page)

            m = _ORDER_URL_RE.match(final_url)
            order_url = m.group(0) if m else final_url
            diag["stage"] = "done"
            diag["order_url"] = order_url
            log.info("Held %d seat(s) for showtime %s -> %s", selected, showtime_id, order_url)
            return await _finish(diag, page)
    except Exception as exc:
        diag["error"] = f"{type(exc).__name__}: {exc}"
        log.error("Hold[%s]: failed at stage=%s: %s", showtime_id, diag["stage"], exc)
        return await _finish(diag, page)
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass


async def _finish(diag: dict, page) -> dict:
    """Best-effort: capture where the browser ended up, for diagnostics."""
    if page is not None:
        try:
            diag["final_url"] = page.url
        except Exception:
            pass
        try:
            diag["final_title"] = await page.title()
        except Exception:
            pass
    return diag


async def _dismiss_cookie_banner(page):
    for sel in _CLOSE_BANNER_SELECTORS:
        try:
            await page.locator(sel).first.click(timeout=2000)
            return
        except Exception:
            continue


async def _click_continue(page) -> bool:
    try:
        await page.click("text=Continue", timeout=8000)
        return True
    except Exception:
        return False


async def _select_seats(page, seat_names: list[str], num_seats: int) -> int:
    """Click up to `num_seats` open seat checkboxes on the live page,
    preferring seats named in `seat_names` (in order) when still open.

    Re-checks openness live rather than trusting `seat_names` blindly, since
    a little time may have passed since that list was gathered.
    """
    checkboxes = page.locator('input[type="checkbox"][aria-label]')
    count = await checkboxes.count()

    open_seats = []  # (index, seat_code)
    for i in range(count):
        cb = checkboxes.nth(i)
        try:
            if await cb.is_disabled():
                continue
            label = (await cb.get_attribute("aria-label")) or ""
        except Exception:
            continue
        code = label.strip().split(" ")[-1] if label else ""
        open_seats.append((i, code))

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
