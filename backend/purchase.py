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
    """Select up to `num_seats` open seats (preferring `seat_names`, in
    order, when they're still open) and create a guest AMC order.

    Returns the order URL to hand to a human for checkout, or None if a hold
    couldn't be created (sold out by the time we got there, page changed
    shape, timed out, etc.) — callers should treat this as best-effort and
    fall back to a regular "here's the showtime" notification.
    """
    if not showtime_id:
        return None
    num_seats = max(1, num_seats)
    seat_names = seat_names or []

    try:
        browser_ = await br._get_browser()
    except Exception as exc:
        log.error("Chromium unavailable for seat hold (%s)", exc)
        return None

    seats_url = f"https://www.amctheatres.com/showtimes/{showtime_id}/seats"
    context = None
    try:
        async with br._page_sem:
            context = await browser_.new_context(
                user_agent=br.BROWSER_UA,
                viewport={"width": 1400, "height": 1100},
                locale="en-US",
            )
            page = await context.new_page()
            await page.goto(seats_url, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)
            await _dismiss_cookie_banner(page)

            selected = await _select_seats(page, seat_names, num_seats)
            if selected == 0:
                log.warning("Hold: no open seats to select for showtime %s (sold out?)", showtime_id)
                return None

            if not await _click_continue(page):
                log.warning("Hold: couldn't advance past seat selection for showtime %s", showtime_id)
                return None

            await page.wait_for_timeout(1500)
            for _ in range(selected):
                try:
                    await page.click('button[aria-label="Add Adult Ticket"]', timeout=5000)
                except Exception:
                    break

            if not await _click_continue(page):
                log.warning("Hold: couldn't advance past ticket selection for showtime %s", showtime_id)
                return None

            try:
                await page.wait_for_url(re.compile(r".*/orders/.*"), timeout=20000)
            except Exception:
                pass
            final_url = page.url

            if "/orders/" not in final_url:
                log.warning(
                    "Hold: flow for showtime %s didn't reach an order (landed on %s)",
                    showtime_id, final_url,
                )
                return None

            m = _ORDER_URL_RE.match(final_url)
            order_url = m.group(0) if m else final_url
            log.info("Held %d seat(s) for showtime %s -> %s", selected, showtime_id, order_url)
            return order_url
    except Exception as exc:
        log.error("Seat hold failed for showtime %s: %s", showtime_id, exc)
        return None
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass


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
