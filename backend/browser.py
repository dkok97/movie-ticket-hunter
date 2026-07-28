"""Headless-browser fetching (Playwright + Chromium).

AMC's public website sits behind a Queue-it "Global Safety Net" waiting room
and renders showtimes/seat maps client-side (Next.js). Plain HTTP requests get
the challenge/queue page instead of real content. A real browser executes the
JS, passes the (runtime-disabled) queue, and renders the page — then we read
the fully-rendered HTML and hand it to the existing parsers.

This module lazily launches a single shared Chromium instance and hands out a
fresh context per fetch. If Playwright/Chromium isn't installed, fetches return
None and the caller degrades gracefully.
"""

import asyncio
import logging
import os

log = logging.getLogger("odyssey.browser")

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_playwright = None
_browser = None
_lock = asyncio.Lock()

# Global cap on concurrent browser contexts. Seat verification can request one
# page per show, and the date sweep is itself concurrent, so without a global
# limit we could spawn many Chromium contexts at once and exhaust memory.
_MAX_CONCURRENT = int(os.environ.get("BROWSER_CONCURRENCY", "4") or "4")
_page_sem = asyncio.Semaphore(_MAX_CONCURRENT)


async def _get_browser():
    """Return a live shared browser, launching it on first use."""
    global _playwright, _browser

    if _browser is not None and _browser.is_connected():
        return _browser

    async with _lock:
        if _browser is not None and _browser.is_connected():
            return _browser
        # Imported lazily so the app still runs where Playwright isn't installed.
        from playwright.async_api import async_playwright

        _playwright = await async_playwright().start()
        launch_kwargs = dict(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        # Allow pointing at a prebuilt Chromium (e.g. an image that ships one).
        exe = os.environ.get("PLAYWRIGHT_EXECUTABLE_PATH")
        if exe:
            launch_kwargs["executable_path"] = exe
        _browser = await _playwright.chromium.launch(**launch_kwargs)
        log.info("Launched headless Chromium")
        return _browser


def looks_like_queue_page(html: str) -> bool:
    """Heuristic: did we land on the Queue-it waiting room instead of content?"""
    if not html:
        return True
    lowered = html[:5000].lower()
    return any(
        marker in lowered
        for marker in ("queue-it", "queue.amctheatres", "you are now in line", "waiting room")
    )


async def fetch_rendered(
    url: str,
    wait_selector: str | None = None,
    wait_ms: int = 6000,
    timeout_ms: int = 45000,
) -> str | None:
    """Navigate to `url` and return the fully-rendered HTML, or None on failure."""
    try:
        browser = await _get_browser()
    except Exception as exc:
        log.error("Chromium unavailable (%s) — is Playwright installed?", exc)
        return None

    context = None
    try:
      async with _page_sem:
        context = await browser.new_context(
            user_agent=BROWSER_UA,
            viewport={"width": 1280, "height": 2000},
            locale="en-US",
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

        # Let the queue redirect + client-side render settle.
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass

        if wait_selector:
            try:
                await page.wait_for_selector(wait_selector, timeout=wait_ms)
            except Exception:
                # Selector may legitimately be absent (e.g. all sold out).
                pass
        else:
            await page.wait_for_timeout(min(wait_ms, 4000))

        html = await page.content()
        if looks_like_queue_page(html):
            log.warning("Rendered page still looks like the Queue-it waiting room: %s", url)
        return html
    except Exception as exc:
        log.error("Browser fetch failed for %s: %s", url, exc)
        return None
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass


async def shutdown():
    """Close the shared browser on app shutdown."""
    global _playwright, _browser
    try:
        if _browser is not None:
            await _browser.close()
    except Exception:
        pass
    try:
        if _playwright is not None:
            await _playwright.stop()
    except Exception:
        pass
    _browser = None
    _playwright = None
