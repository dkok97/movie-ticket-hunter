"""Ticket availability checkers for AMC and Fandango.

Each checker tries multiple strategies (API → embedded JSON → HTML scraping)
and returns a list of finding dicts.
"""

import asyncio
import httpx
import json
import re
import logging
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

import config as cfg

log = logging.getLogger("odyssey.checker")

# Markers that indicate a showtime is NOT actually bookable.
SOLD_OUT_RE = re.compile(
    r"sold\s*out|no longer available|not available|unavailable|past\b", re.IGNORECASE
)
# Premium-format markers.
FORMAT_70MM_RE = re.compile(r"70\s*-?\s*mm", re.IGNORECASE)
IMAX_RE = re.compile(r"imax", re.IGNORECASE)

# Common browser-like headers to avoid being blocked
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

TIMEOUT = httpx.Timeout(20.0, connect=10.0)


# ===========================================================================
# AMC Checker
# ===========================================================================

async def check_amc(theatre: dict, movie_name: str, target_date: str) -> list[dict]:
    """Check AMC for ticket availability.

    When an AMC Developer API key is configured, the official API is the
    authoritative source (clean JSON, and it bypasses AMC's Queue-it anti-bot
    waiting room that intercepts the public website). We only fall back to web
    scraping if the API call itself errors — an *empty* API result means
    "genuinely no open seats", not "try scraping instead".
    """
    conf = cfg.get_config()
    api_key = conf.get("amc_api_key", "")
    vendor_id = conf.get("amc_vendor_id", "")
    movie_slug = conf.get("amc_movie_slug", "")
    use_browser = str(conf.get("use_browser", True)).lower() not in ("false", "0", "no")

    # Strategy 0 (preferred): AMC Developer API.
    if api_key:
        api_findings = await _amc_developer_api(theatre, movie_name, target_date, api_key, vendor_id)
        if api_findings is not None:
            return api_findings  # authoritative, even when empty
        log.warning("AMC API unavailable for %s — falling back to browser/scraping", target_date)

    # Strategy 1 (preferred fallback): headless browser — defeats Queue-it and
    # the client-side rendering that make plain HTTP scraping useless.
    if use_browser and movie_slug:
        findings = await _amc_browser_movie_page(movie_slug, theatre, movie_name, target_date)
        if findings:
            findings = await _enrich_findings_with_seats(findings, _browser_fetch_open_seats)
        return findings

    findings: list[dict] = []

    # Strategy 2: Direct AMC movie page via plain HTTP (unreliable behind Queue-it)
    if movie_slug:
        findings = await _amc_direct_movie_page(movie_slug, theatre, movie_name, target_date)
        if findings:
            return findings

    # Strategy 3: AMC web — showtime page
    findings = await _amc_showtime_page(theatre, movie_name, target_date)
    if findings:
        return findings

    # Strategy 4: AMC web — movie search
    findings = await _amc_movie_search(movie_name, target_date, theatre)

    return findings


async def _amc_direct_movie_page(
    movie_slug: str, theatre: dict, movie_name: str, target_date: str
) -> list[dict]:
    """Check the known AMC movie page directly for showtime availability.

    URL: https://www.amctheatres.com/movies/{slug}/showtimes?date={date}
    This is the most reliable check — it's the official page for the movie.
    """
    theatre_slug = theatre.get("slug", "")
    base_movie_url = f"https://www.amctheatres.com/movies/{movie_slug}/showtimes"
    movie_url_with_date = f"{base_movie_url}?date={target_date}"

    # Also try the theatre-specific URL pattern
    urls_to_try = [movie_url_with_date]
    if theatre_slug:
        urls_to_try.append(f"{base_movie_url}/{theatre_slug}/{target_date}")

    for url in urls_to_try:
        log.info("Checking AMC direct movie page: %s", url)
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(url, headers=BROWSER_HEADERS)
                if resp.status_code != 200:
                    log.warning("AMC direct movie page returned %s for %s", resp.status_code, url)
                    continue

                findings = _parse_amc_movie_page(resp.text, theatre, movie_name, target_date, movie_url_with_date)
                if findings:
                    return findings

        except Exception as exc:
            log.error("AMC direct movie page error for %s: %s", url, exc)

    return []


async def _amc_browser_movie_page(
    movie_slug: str, theatre: dict, movie_name: str, target_date: str
) -> list[dict]:
    """Render the AMC movie page in a headless browser, then parse it.

    Uses the same page structure as the plain-HTTP path, but a real browser
    passes Queue-it and executes the client-side rendering, so the showtime
    links actually exist in the returned HTML.
    """
    import browser as br

    theatre_slug = theatre.get("slug", "")
    base = f"https://www.amctheatres.com/movies/{movie_slug}/showtimes"
    if theatre_slug:
        url = f"{base}/{theatre_slug}/{target_date}"
    else:
        url = f"{base}?date={target_date}"

    html = await br.fetch_rendered(url, wait_selector="a[href^='/showtimes/']", wait_ms=8000)
    if not html:
        log.warning("Browser returned no HTML for %s", url)
        return []

    findings = _parse_amc_movie_page(html, theatre, movie_name, target_date, url)
    log.info("Browser %s: %d open %s showtime(s)", target_date, len(findings),
             cfg.get("movie_format", "") or "movie")
    return findings


def _parse_amc_movie_page(
    html: str, theatre: dict, movie_name: str, target_date: str, movie_url: str
) -> list[dict]:
    """Parse the AMC direct movie page for showtime availability.

    The page at /movies/{slug}/showtimes?date={date} may contain:
    - Embedded JSON with showtime data
    - Theatre selection UI with showtimes per theatre
    - "Get Tickets" / "Reserve" buttons if tickets are on sale
    """
    findings = []
    theatre_name = theatre.get("name", "AMC Theatre")
    theatre_slug = theatre.get("slug", "")
    conf = cfg.get_config()
    evening_hour = int(conf.get("evening_start_hour", 16))
    movie_format = conf.get("movie_format", "")

    soup = BeautifulSoup(html, "html.parser")

    # --- Strategy A: Look for embedded JSON with showtime data ---
    for script in soup.find_all("script"):
        script_text = script.string or ""
        if not script_text.strip():
            continue

        # Look for preloaded state / next data / JSON-LD
        for pattern in [
            r'window\.__PRELOADED_STATE__\s*=\s*({.*?});?\s*$',
            r'window\.__NEXT_DATA__\s*=\s*({.*?});?\s*$',
        ]:
            match = re.search(pattern, script_text, re.DOTALL | re.MULTILINE)
            if match:
                try:
                    data = json.loads(match.group(1))
                    showtimes = _extract_theatre_showtimes(data, theatre_slug, theatre_name, evening_hour)
                    if showtimes:
                        for st in showtimes:
                            findings.append({
                                "source": "amc_direct",
                                "theatre_name": theatre_name,
                                "movie_name": movie_name,
                                "showtime": st.get("time", ""),
                                "show_date": target_date,
                                "ticket_url": st.get("url", movie_url),
                            })
                        return findings
                except (json.JSONDecodeError, TypeError):
                    continue

        # Also check for JSON-LD structured data
        if script.get("type") in ("application/ld+json", "application/json"):
            try:
                data = json.loads(script_text)
                if isinstance(data, dict):
                    # Check for screening event data
                    event_type = data.get("@type", "")
                    if "Event" in event_type or "Screening" in event_type:
                        location = data.get("location", {})
                        loc_name = location.get("name", "") if isinstance(location, dict) else ""
                        if not theatre_slug or theatre_slug.replace("-", " ") in loc_name.lower().replace("-", " "):
                            start = data.get("startDate", "")
                            url = data.get("url", movie_url)
                            findings.append({
                                "source": "amc_direct",
                                "theatre_name": loc_name or theatre_name,
                                "movie_name": movie_name,
                                "showtime": start,
                                "show_date": target_date,
                                "ticket_url": url,
                            })
            except (json.JSONDecodeError, TypeError):
                pass

    # --- Strategy B: Look for showtime links with href="/showtimes/{id}" ---
    # AMC showtime links look like:
    #   <a ... href="/showtimes/140692343" aria-describedby="...amc-lincoln-square-13...">8:00pm</a>
    # The direct seat-selection URL is: /showtimes/{id}/seats
    theatre_lower = theatre_name.lower()
    theatre_slug_normalised = theatre_slug.replace("-", " ") if theatre_slug else ""

    showtime_links = soup.find_all("a", href=re.compile(r"^/showtimes/\d+$"))
    for link in showtime_links:
        # Check if this showtime belongs to our theatre using aria-describedby
        aria = link.get("aria-describedby", "")
        aria_lower = aria.lower().replace("-", " ")

        # Also check parent section text for theatre name
        parent_has_theatre = False
        for parent in link.parents:
            parent_text = parent.get_text(" ", strip=True).lower()
            if theatre_lower in parent_text:
                parent_has_theatre = True
                break
            if parent.name in ("body", "html", "main"):
                break

        theatre_in_aria = (
            (theatre_slug_normalised and theatre_slug_normalised in aria_lower) or
            (theatre_lower.replace("amc ", "") in aria_lower)
        )

        if not theatre_in_aria and not parent_has_theatre:
            continue

        # Skip showtimes that are sold out / not bookable — we only want
        # showtimes that actually have open seats.
        if _is_sold_out(link):
            continue

        href = link.get("href", "")  # e.g. "/showtimes/140692343"
        raw_text = link.get_text(" ", strip=True)  # e.g. "2:00pm UP TO 15% OFF Almost Full"
        link_text = _clean_showtime(raw_text)       # -> "2:00pm"
        status = _status_from_text(raw_text)         # -> "Almost Full"

        # Build the direct seat-selection URL
        seat_url = f"https://www.amctheatres.com{href}/seats"
        showtime_id = href.rstrip("/").split("/")[-1]

        if _is_evening_time(link_text, evening_hour):
            findings.append({
                "source": "amc_direct",
                "theatre_name": theatre_name,
                "movie_name": movie_name,
                "showtime": link_text,
                "status": status,
                "show_date": target_date,
                "ticket_url": seat_url,
                # This page is the format-specific IMAX 70mm event page, so
                # every showtime on it is already the right format.
                "format": movie_format,
                "available": True,
                "showtime_id": showtime_id,
            })

    # --- Strategy C: Check if the page mentions the theatre at all ---
    if not findings:
        page_text = html.lower()
        theatre_mentioned = theatre_lower in page_text or (
            theatre_slug and theatre_slug.replace("-", " ") in page_text
        )

        # Look for "Get Tickets", "Reserve", "Buy" buttons
        buy_buttons = soup.find_all(
            ["a", "button"],
            string=re.compile(r"(get ticket|buy ticket|reserve|select seat)", re.IGNORECASE),
        )

        if buy_buttons and theatre_mentioned:
            btn = buy_buttons[0]
            href = btn.get("href", "")
            ticket_url = f"https://www.amctheatres.com{href}" if href and href.startswith("/") else movie_url
            findings.append({
                "source": "amc_direct",
                "theatre_name": theatre_name,
                "movie_name": movie_name,
                "showtime": "Tickets available (check site for times)",
                "show_date": target_date,
                "ticket_url": ticket_url,
            })

    return findings


def _extract_theatre_showtimes(
    data: dict | list, theatre_slug: str, theatre_name: str, evening_hour: int
) -> list[dict]:
    """Recursively extract showtimes for a specific theatre from embedded JSON."""
    results = []

    if isinstance(data, dict):
        # Check if this dict looks like a theatre or showtime object
        d_name = (data.get("name", "") or data.get("theatreName", "") or "").lower()
        d_slug = (data.get("slug", "") or data.get("theatreSlug", "") or "").lower()
        d_id = str(data.get("theatreId", "") or data.get("id", ""))

        is_our_theatre = (
            (theatre_slug and theatre_slug in d_slug) or
            (theatre_slug and theatre_slug.replace("-", " ") in d_name) or
            (theatre_name.lower() in d_name)
        )

        if is_our_theatre:
            # Found our theatre — look for showtimes within this subtree
            inner_times = _extract_times_from_subtree(data, evening_hour)
            results.extend(inner_times)

        # Recurse
        for v in data.values():
            results.extend(_extract_theatre_showtimes(v, theatre_slug, theatre_name, evening_hour))

    elif isinstance(data, list):
        for item in data:
            results.extend(_extract_theatre_showtimes(item, theatre_slug, theatre_name, evening_hour))

    return results


def _extract_times_from_subtree(data: dict | list, evening_hour: int) -> list[dict]:
    """Extract showtime strings from a JSON subtree."""
    results = []

    if isinstance(data, dict):
        time_str = (
            data.get("showDateTimeLocal", "")
            or data.get("startDate", "")
            or data.get("performanceDateTime", "")
            or data.get("showtime", "")
        )
        url = data.get("purchaseUrl", "") or data.get("url", "") or data.get("ticketUrl", "")

        if time_str:
            try:
                dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                if dt.hour >= evening_hour:
                    results.append({"time": dt.strftime("%I:%M %p"), "url": url})
            except Exception:
                results.append({"time": time_str, "url": url})

        for v in data.values():
            results.extend(_extract_times_from_subtree(v, evening_hour))

    elif isinstance(data, list):
        for item in data:
            results.extend(_extract_times_from_subtree(item, evening_hour))

    return results


async def _amc_developer_api(
    theatre: dict, movie_name: str, target_date: str, api_key: str, vendor_id: str
) -> list[dict] | None:
    """Query the AMC Developer API for showtimes.

    Returns a list of findings on success (possibly empty = no open seats),
    or None if the request failed (so the caller can fall back to scraping).

    Auth is a single ``X-AMC-Vendor-Key`` header. ``vendor_id`` is unused for
    catalog calls but kept for backward compatibility.
    """
    theatre_id = theatre.get("theatre_id", "")
    if not theatre_id:
        log.warning("AMC API: theatre %r has no theatre_id; cannot use API", theatre.get("name"))
        return None

    headers = {
        "X-AMC-Vendor-Key": api_key,
        "Accept": "application/json",
    }
    # Large pageSize to minimise pagination; still follow next links as a safety net.
    url = f"https://api.amctheatres.com/v2/theatres/{theatre_id}/showtimes/{target_date}?pageSize=100"

    all_showtimes: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            for _ in range(20):  # hard page cap
                resp = await client.get(url, headers=headers)
                if resp.status_code in (401, 403):
                    log.error(
                        "AMC API auth rejected (%s) — check AMC_API_KEY (X-AMC-Vendor-Key)",
                        resp.status_code,
                    )
                    return None
                if resp.status_code == 404:
                    # No showtimes published for this theatre/date — a valid empty result.
                    return []
                if resp.status_code != 200:
                    log.warning(
                        "AMC API returned %s for theatre %s date %s",
                        resp.status_code, theatre_id, target_date,
                    )
                    return None

                data = resp.json()
                page = data.get("_embedded", {}).get("showtimes", []) or data.get("showtimes", [])
                all_showtimes.extend(page)

                links = data.get("_links", {}) or {}
                nxt = links.get("next", {})
                next_href = nxt.get("href", "") if isinstance(nxt, dict) else ""
                if not next_href:
                    break
                url = next_href

            findings = _parse_amc_api_response(
                {"_embedded": {"showtimes": all_showtimes}}, movie_name, theatre, target_date
            )
            # Enrich with seat-level detail (best effort) while the client is open.
            if findings:
                async def _seat_fetcher(sid, _tid=theatre_id, _key=api_key, _client=client):
                    return await _amc_fetch_open_seats(_tid, sid, _key, _client)

                findings = await _enrich_findings_with_seats(findings, _seat_fetcher)
    except Exception as exc:
        log.error("AMC API error for %s: %s", target_date, exc)
        return None

    log.info(
        "AMC API %s: %d showtime(s) at theatre, %d open %s match(es)",
        target_date, len(all_showtimes), len(findings), cfg.get("movie_format", "") or "movie",
    )
    return findings


def _amc_api_purchase_url(st: dict) -> str:
    """Best purchase/seat-selection URL for an API showtime object."""
    links = st.get("_links", {}) or {}
    # Any HAL rel that looks like a purchase link.
    for rel, val in links.items():
        if "purchase" in rel.lower() or "ticket" in rel.lower():
            if isinstance(val, dict) and val.get("href"):
                return val["href"]
            if isinstance(val, list) and val and isinstance(val[0], dict) and val[0].get("href"):
                return val[0]["href"]
    # Fallback: build the website seat-selection URL from the performance id.
    st_id = st.get("id", "")
    if st_id:
        return f"https://www.amctheatres.com/showtimes/{st_id}/seats"
    return "https://www.amctheatres.com"


def _parse_amc_api_response(
    data: dict, movie_name: str, theatre: dict, target_date: str
) -> list[dict]:
    """Parse the AMC Developer API showtime response.

    Filters to the target movie, drops sold-out showtimes, and requires the
    premium format (e.g. IMAX 70mm) to match the showtime's attributes.
    """
    findings = []
    movie_lower = movie_name.lower()
    conf = cfg.get_config()
    evening_hour = int(conf.get("evening_start_hour", 16))
    movie_format = conf.get("movie_format", "")

    showtimes = data.get("_embedded", {}).get("showtimes", [])
    if not showtimes:
        showtimes = data.get("showtimes", [])

    for st in showtimes:
        st_movie = st.get("movieName", "") or (st.get("movie", {}) or {}).get("name", "")
        if movie_lower not in st_movie.lower():
            continue

        # Skip sold-out / cancelled shows — we only want bookable seats.
        # (isAlmostSoldOut still has seats, so it is kept.)
        if st.get("isSoldOut") is True or st.get("isCanceled") is True:
            continue

        # Require the premium format. AMC exposes it via the attributes list.
        attrs = st.get("attributes", []) or []
        attr_text = " ".join(filter(None, [
            st_movie,
            st.get("premiumFormat", "") if isinstance(st.get("premiumFormat"), str) else "",
            " ".join(
                f"{a.get('code', '')} {a.get('name', '')} {a.get('description', '')}"
                for a in attrs if isinstance(a, dict)
            ),
        ]))
        if not _matches_format(attr_text, movie_format):
            continue

        showtime_str = (
            st.get("showDateTimeLocal", "")
            or st.get("showDateTimeUtc", "")
            or st.get("performanceDateTime", "")
        )
        # Filter for evening shows (evening_start_hour=0 keeps everything)
        try:
            dt = datetime.fromisoformat(showtime_str.replace("Z", "+00:00"))
            if dt.hour < evening_hour:
                continue
            nice_time = dt.strftime("%I:%M %p")
        except Exception:
            nice_time = showtime_str

        purchase_url = _amc_api_purchase_url(st)

        findings.append({
            "source": "amc_api",
            "theatre_name": theatre.get("name", "AMC Theatre"),
            "movie_name": st_movie,
            "showtime": nice_time,
            "status": "Almost Full" if st.get("isAlmostSoldOut") is True else "",
            "show_date": target_date,
            "ticket_url": purchase_url,
            "format": movie_format,
            "available": True,
            "showtime_id": str(st.get("id", "")),
        })

    return findings


# ===========================================================================
# Seat-level availability (AMC API)
# ===========================================================================

# Seat types/labels we treat as accessible and want to avoid.
ACCESSIBLE_SEAT_RE = re.compile(
    r"wheel\s*chair|companion|accessible|handicap|\bada\b", re.IGNORECASE
)


def _seat_label(seat: dict) -> str:
    return str(seat.get("name") or seat.get("label") or seat.get("id") or "").strip()


def _seat_is_accessible(seat: dict) -> bool:
    hay = " ".join(
        str(seat.get(k, "")) for k in ("type", "seatType", "name", "description", "category")
    )
    if ACCESSIBLE_SEAT_RE.search(hay):
        return True
    for k in ("isWheelchair", "wheelchair", "isCompanion", "companion",
              "isAccessible", "accessible", "isHandicap"):
        if seat.get(k) is True:
            return True
    return False


def _seat_is_available(seat: dict):
    """True/False if determinable, else None."""
    for k in ("available", "isAvailable", "open", "isOpen"):
        if isinstance(seat.get(k), bool):
            return seat[k]
    status = str(
        seat.get("status", "") or seat.get("state", "") or seat.get("seatStatus", "")
    ).lower()
    if status:
        if any(w in status for w in ("avail", "open", "free", "empty")):
            return True
        if any(w in status for w in ("taken", "sold", "occupied", "unavail", "reserved", "broken", "blocked", "held")):
            return False
    for k in ("isTaken", "taken", "sold", "occupied", "reserved"):
        if isinstance(seat.get(k), bool):
            return not seat[k]
    return None


def _extract_largest_embedded_json(html: str):
    """Extract embedded page-state JSON (e.g. __NEXT_DATA__) from rendered HTML.

    Returns the parsed object, or None. Used to pull the seat layout out of the
    client-rendered AMC seat-selection page.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Next.js pages expose all page data in <script id="__NEXT_DATA__">.
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            return json.loads(tag.string)
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallbacks: window.__PRELOADED_STATE__ / other JSON script blocks.
    best = None
    best_len = 0
    for pattern in (
        r'window\.__PRELOADED_STATE__\s*=\s*({.*?})\s*;?\s*</script>',
        r'window\.__NEXT_DATA__\s*=\s*({.*?})\s*;?\s*</script>',
    ):
        for match in re.findall(pattern, html, re.DOTALL):
            if len(match) > best_len:
                try:
                    parsed = json.loads(match)
                    best, best_len = parsed, len(match)
                except (json.JSONDecodeError, TypeError):
                    continue
    return best


def _collect_seats(data) -> list[dict]:
    """Recursively find seat-like dicts in a seat-availability response."""
    seats: list[dict] = []
    if isinstance(data, dict):
        label = data.get("name") or data.get("label") or data.get("id")
        looks_like_seat = bool(label) and any(
            k in data for k in (
                "available", "isAvailable", "status", "seatStatus", "isTaken",
                "type", "seatType", "rowIndex", "columnIndex",
            )
        )
        if looks_like_seat and isinstance(label, (str, int)):
            seats.append(data)
        for v in data.values():
            seats.extend(_collect_seats(v))
    elif isinstance(data, list):
        for item in data:
            seats.extend(_collect_seats(item))
    return seats


def _split_open_seats(data) -> tuple[list[str], list[str]]:
    """From any seat-bearing JSON, return (open_standard, open_accessible) labels."""
    open_std, open_acc = [], []
    for s in _collect_seats(data):
        if _seat_is_available(s) is not True:
            continue
        (open_acc if _seat_is_accessible(s) else open_std).append(_seat_label(s))
    return open_std, open_acc


async def _amc_fetch_open_seats(
    theatre_id: str, showtime_id: str, api_key: str, client: httpx.AsyncClient
) -> tuple[list[str], list[str]] | None:
    """Return (open_standard_labels, open_accessible_labels) or None if unavailable.

    Tries a couple of plausible AMC seat-availability endpoints. Seat-level data
    may be part of AMC's restricted ecommerce API, so this degrades gracefully
    to None when the key isn't permitted.
    """
    if not showtime_id:
        return None

    urls = [
        f"https://api.amctheatres.com/v2/theatres/{theatre_id}/showtimes/{showtime_id}/seats",
        f"https://api.amctheatres.com/v2/showtimes/{showtime_id}/seats",
    ]
    headers = {"X-AMC-Vendor-Key": api_key, "Accept": "application/json"}

    for url in urls:
        try:
            resp = await client.get(url, headers=headers)
        except Exception as exc:
            log.warning("AMC seat fetch error for showtime %s: %s", showtime_id, exc)
            return None
        if resp.status_code in (401, 403):
            log.warning(
                "AMC seat data not permitted for this key (%s) — reporting showtime without seat detail",
                resp.status_code,
            )
            return None
        if resp.status_code == 404:
            continue  # try next URL shape
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except Exception:
            return None

        if not _collect_seats(data):
            return None
        return _split_open_seats(data)

    return None


async def _browser_fetch_open_seats(showtime_id: str) -> tuple[list[str], list[str]] | None:
    """Fetch the rendered seat-selection page and extract open seats via a browser."""
    if not showtime_id:
        return None
    import browser as br

    url = f"https://www.amctheatres.com/showtimes/{showtime_id}/seats"
    html = await br.fetch_rendered(url, wait_selector=None, wait_ms=6000)
    if not html:
        return None
    data = _extract_largest_embedded_json(html)
    if data is None or not _collect_seats(data):
        return None
    return _split_open_seats(data)


async def _enrich_findings_with_seats(findings: list[dict], seat_fetcher) -> list[dict]:
    """Attach seat detail to findings and drop shows with no non-accessible seats.

    `seat_fetcher` is an async callable taking a showtime_id and returning
    (open_standard, open_accessible) or None. Controlled by config: report_seats
    (default on) and exclude_accessible_seats (default on). If seat data can't be
    retrieved, findings are kept as-is (no seat list).
    """
    conf = cfg.get_config()
    report_seats = str(conf.get("report_seats", True)).lower() not in ("false", "0", "no")
    exclude_accessible = str(conf.get("exclude_accessible_seats", True)).lower() not in ("false", "0", "no")
    if not report_seats:
        return findings

    # Bound seat lookups per cycle — each is a network/browser round-trip, and a
    # wide date range can produce dozens of open showtimes. Beyond the cap,
    # findings are kept without a seat list.
    max_lookups = int(conf.get("max_seat_lookups", 12) or 12)

    kept: list[dict] = []
    for idx, f in enumerate(findings):
        result = None if idx >= max_lookups else await seat_fetcher(f.get("showtime_id", ""))
        if result is None:
            # Seat detail unavailable — keep the finding, just no seat list.
            f["seat_summary"] = ""
            kept.append(f)
            continue

        open_std, open_acc = result
        usable = open_std if exclude_accessible else (open_std + open_acc)

        if not usable:
            # Only accessible seats left (or none) — skip per "avoid handicap seats".
            log.info(
                "Skipping %s %s: no non-accessible seats (%d accessible only)",
                f.get("show_date"), f.get("showtime"), len(open_acc),
            )
            continue

        preview = ", ".join(usable[:12]) + ("…" if len(usable) > 12 else "")
        f["available_seats"] = usable
        f["seat_summary"] = f"{len(usable)} open seat(s): {preview}"
        if exclude_accessible and open_acc:
            f["seat_summary"] += f" (excluded {len(open_acc)} accessible)"
        kept.append(f)

    return kept


async def _amc_showtime_page(theatre: dict, movie_name: str, target_date: str) -> list[dict]:
    """Scrape the AMC website showtime page for a specific theatre and date.

    Tries two URL patterns:
      1. /movie-theatres/{region}/{slug}/showtimes?date={date}  (the main page users see)
      2. /showtimes/all/{date}/{slug}/all  (legacy/API-style URL)
    """
    slug = theatre.get("slug", "")
    if not slug:
        return []

    region = theatre.get("region", "")

    urls_to_try = []
    if region:
        # Primary: the theatre-specific page (same format as the screenshot)
        urls_to_try.append(
            f"https://www.amctheatres.com/movie-theatres/{region}/{slug}/showtimes?date={target_date}"
        )
    # Fallback: legacy showtime URL
    urls_to_try.append(f"https://www.amctheatres.com/showtimes/all/{target_date}/{slug}/all")

    for url in urls_to_try:
        log.info("Checking AMC theatre page: %s", url)
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(url, headers=BROWSER_HEADERS)
                if resp.status_code != 200:
                    log.warning("AMC theatre page returned %s for %s", resp.status_code, url)
                    continue

                findings = _parse_amc_html(resp.text, movie_name, theatre, target_date, url)
                if findings:
                    return findings
        except Exception as exc:
            log.error("AMC web scraping error for %s: %s", url, exc)

    return []


def _find_amc_movie_url(soup: BeautifulSoup, html: str, movie_lower: str, target_date: str) -> str:
    """Extract the AMC movie-specific showtime URL from page HTML.

    Looks for /movies/{slug} links whose text matches the movie name and builds:
      https://www.amctheatres.com/movies/{slug}/showtimes?date={date}
    """
    # Strategy 1: <a> tags with /movies/ hrefs
    for link in soup.find_all("a", href=re.compile(r"/movies/", re.IGNORECASE)):
        href = link.get("href", "")
        link_text = link.get_text(" ", strip=True).lower()
        if movie_lower in link_text:
            # href might be "/movies/dracula-81573" or full URL
            slug = href.split("/movies/")[-1].split("/")[0].split("?")[0]
            if slug:
                return f"https://www.amctheatres.com/movies/{slug}/showtimes?date={target_date}"

    # Strategy 2: regex scan for /movies/{slug} anywhere in raw HTML near the movie name
    pattern = r'/movies/([\w-]+)'
    for m in re.finditer(pattern, html):
        slug = m.group(1)
        # Check if the movie name words appear near this slug in the text
        start = max(0, m.start() - 300)
        end = min(len(html), m.end() + 300)
        context = html[start:end].lower()
        if movie_lower in context:
            return f"https://www.amctheatres.com/movies/{slug}/showtimes?date={target_date}"

    return ""


def _parse_amc_html(
    html: str, movie_name: str, theatre: dict, target_date: str, page_url: str
) -> list[dict]:
    """Parse AMC showtime page HTML for ticket availability."""
    findings = []
    movie_lower = movie_name.lower()
    conf = cfg.get_config()
    evening_hour = int(conf.get("evening_start_hour", 16))
    movie_format = conf.get("movie_format", "")

    # Strategy A: Look for embedded JSON (window.__PRELOADED_STATE__ or similar)
    json_findings = _extract_embedded_json(html, movie_lower, theatre, target_date, evening_hour)
    if json_findings:
        return json_findings

    # Strategy B: Parse HTML with BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    # Try to find the proper movie-specific URL first
    movie_url = _find_amc_movie_url(soup, html, movie_lower, target_date) or page_url

    # Strategy B1: Look for showtime links with href="/showtimes/{id}" near our movie
    # These are the actual clickable showtime buttons on AMC pages.
    # The link text is the time (e.g. "8:00pm"), and the aria-describedby
    # contains movie/theatre slugs for context.
    showtime_links = soup.find_all("a", href=re.compile(r"^/showtimes/\d+$"))
    for link in showtime_links:
        raw_text = link.get_text(" ", strip=True)
        link_text = _clean_showtime(raw_text)  # e.g. "8:00pm"
        status = _status_from_text(raw_text)
        aria = (link.get("aria-describedby", "") or "").lower()

        # Check if this showtime belongs to our movie
        movie_words = movie_lower.replace(":", "").split()
        movie_in_aria = all(w in aria.replace("-", " ") for w in movie_words if len(w) > 2)

        # Also check parent section text
        movie_in_parent = False
        if not movie_in_aria:
            for parent in link.parents:
                pt = parent.get_text(" ", strip=True).lower()
                if movie_lower in pt:
                    movie_in_parent = True
                    break
                if parent.name in ("body", "html", "main"):
                    break

        if not movie_in_aria and not movie_in_parent:
            continue

        # This page lists every movie/format for the theatre, so require the
        # premium format (IMAX 70mm) to match and skip sold-out showtimes.
        context = _element_context(link)
        if not _matches_format(context, movie_format):
            continue
        if _is_sold_out(link):
            continue

        href = link.get("href", "")
        seat_url = f"https://www.amctheatres.com{href}/seats"

        if _is_evening_time(link_text, evening_hour):
            findings.append({
                "source": "amc_web",
                "theatre_name": theatre.get("name", "AMC Theatre"),
                "movie_name": movie_name,
                "showtime": link_text,
                "status": status,
                "show_date": target_date,
                "ticket_url": seat_url,
                "format": movie_format,
                "available": True,
            })

    if findings:
        return findings

    # Strategy B2: Fallback — look for movie sections with showtime elements
    movie_sections = soup.find_all(
        ["div", "section", "article"],
        class_=re.compile(r"(showtime|movie|film)", re.IGNORECASE),
    )

    for section in movie_sections:
        section_text = section.get_text(" ", strip=True).lower()
        if movie_lower not in section_text:
            continue

        # Found the movie — look for any showtime links inside
        time_elements = section.find_all(
            ["a", "button", "span", "time"],
            class_=re.compile(r"(showtime|time|btn)", re.IGNORECASE),
        )

        for elem in time_elements:
            time_text = elem.get_text(strip=True)
            href = elem.get("href", "")
            # If it's a /showtimes/{id} link, build seat URL
            if href and re.match(r"^/showtimes/\d+$", href):
                ticket_url = f"https://www.amctheatres.com{href}/seats"
            elif href and href.startswith("/"):
                ticket_url = f"https://www.amctheatres.com{href}"
            else:
                ticket_url = href

            if _is_evening_time(time_text, evening_hour):
                findings.append({
                    "source": "amc_web",
                    "theatre_name": theatre.get("name", "AMC Theatre"),
                    "movie_name": movie_name,
                    "showtime": time_text,
                    "show_date": target_date,
                    "ticket_url": ticket_url or movie_url,
                })

        if not time_elements:
            findings.append({
                "source": "amc_web",
                "theatre_name": theatre.get("name", "AMC Theatre"),
                "movie_name": movie_name,
                "showtime": "Available (check site for times)",
                "show_date": target_date,
                "ticket_url": movie_url,
            })

    # Strategy C: Simple text search as last resort
    if not findings and movie_lower in html.lower():
        findings.append({
            "source": "amc_web",
            "theatre_name": theatre.get("name", "AMC Theatre"),
            "movie_name": movie_name,
            "showtime": "Detected on page (check site for details)",
            "show_date": target_date,
            "ticket_url": movie_url,
        })

    return findings


def _extract_embedded_json(
    html: str, movie_lower: str, theatre: dict, target_date: str, evening_hour: int
) -> list[dict]:
    """Try to extract showtime data from embedded JSON in script tags."""
    findings = []

    # Look for JSON embedded in script tags (common patterns)
    patterns = [
        r'window\.__PRELOADED_STATE__\s*=\s*({.*?});?\s*</script>',
        r'window\.__NEXT_DATA__\s*=\s*({.*?});?\s*</script>',
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, html, re.DOTALL)
        for match in matches:
            try:
                data = json.loads(match)
                # Recursively search for movie name in JSON
                if _json_contains_movie(data, movie_lower):
                    showtimes = _extract_showtimes_from_json(data, movie_lower, evening_hour)
                    for st in showtimes:
                        findings.append({
                            "source": "amc_web_json",
                            "theatre_name": theatre.get("name", "AMC Theatre"),
                            "movie_name": st.get("movie_name", movie_lower.title()),
                            "showtime": st.get("showtime", ""),
                            "show_date": target_date,
                            "ticket_url": st.get("ticket_url", ""),
                        })
                    if not showtimes:
                        findings.append({
                            "source": "amc_web_json",
                            "theatre_name": theatre.get("name", "AMC Theatre"),
                            "movie_name": movie_lower.title(),
                            "showtime": "Found in page data",
                            "show_date": target_date,
                            "ticket_url": "",
                        })
            except (json.JSONDecodeError, TypeError):
                continue

    return findings


def _json_contains_movie(data, movie_lower: str) -> bool:
    """Recursively check if JSON data contains the movie name."""
    if isinstance(data, str):
        return movie_lower in data.lower()
    elif isinstance(data, dict):
        return any(_json_contains_movie(v, movie_lower) for v in data.values())
    elif isinstance(data, list):
        return any(_json_contains_movie(item, movie_lower) for item in data)
    return False


def _extract_showtimes_from_json(data, movie_lower: str, evening_hour: int) -> list[dict]:
    """Try to extract showtime information from parsed JSON."""
    results = []

    if isinstance(data, dict):
        # Check if this dict represents a showtime
        name = data.get("movieName", "") or data.get("name", "") or data.get("title", "")
        if name and movie_lower in name.lower():
            time_str = (
                data.get("showDateTimeLocal", "")
                or data.get("startDate", "")
                or data.get("performanceDateTime", "")
            )
            url = data.get("purchaseUrl", "") or data.get("url", "")
            if time_str:
                results.append({
                    "movie_name": name,
                    "showtime": time_str,
                    "ticket_url": url,
                })

        # Recurse into values
        for v in data.values():
            results.extend(_extract_showtimes_from_json(v, movie_lower, evening_hour))

    elif isinstance(data, list):
        for item in data:
            results.extend(_extract_showtimes_from_json(item, movie_lower, evening_hour))

    return results


async def _amc_movie_search(movie_name: str, target_date: str, theatre: dict) -> list[dict]:
    """Search AMC website for the movie."""
    encoded = quote_plus(movie_name)
    url = f"https://www.amctheatres.com/search?query={encoded}"
    log.info("Searching AMC: %s", url)

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                return []

            movie_lower = movie_name.lower()
            if movie_lower in resp.text.lower():
                # Movie found in search — extract link
                soup = BeautifulSoup(resp.text, "html.parser")
                links = soup.find_all("a", href=re.compile(r"/movies/", re.IGNORECASE))
                for link in links:
                    if movie_lower in link.get_text().lower():
                        href = link.get("href", "")
                        # Extract slug and build a proper showtime URL
                        slug = href.split("/movies/")[-1].split("/")[0].split("?")[0]
                        if slug:
                            movie_url = f"https://www.amctheatres.com/movies/{slug}/showtimes?date={target_date}"
                        else:
                            movie_url = href if href.startswith("http") else f"https://www.amctheatres.com{href}"
                        return [{
                            "source": "amc_search",
                            "theatre_name": theatre.get("name", "AMC"),
                            "movie_name": movie_name,
                            "showtime": "Movie found — check for showtimes",
                            "show_date": target_date,
                            "ticket_url": movie_url,
                        }]
    except Exception as exc:
        log.error("AMC search error: %s", exc)

    return []


# ===========================================================================
# Fandango Checker
# ===========================================================================

async def check_fandango(theatre: dict, movie_name: str, target_date: str) -> list[dict]:
    """Check Fandango for ticket availability."""
    findings: list[dict] = []

    # Strategy 1: Fandango movie search
    findings = await _fandango_movie_search(movie_name, target_date, theatre)
    if findings:
        return findings

    # Strategy 2: Fandango theatre page
    findings = await _fandango_theatre_page(theatre, movie_name, target_date)

    return findings


async def _fandango_movie_search(movie_name: str, target_date: str, theatre: dict) -> list[dict]:
    """Search Fandango for the movie."""
    findings = []
    encoded = quote_plus(movie_name)
    conf = cfg.get_config()
    movie_year = conf.get("movie_year", "2026")

    # Try the autocomplete/search API first
    search_url = f"https://www.fandango.com/nw/csx/mango/search/util/autocomplete/movies?q={encoded}"
    log.info("Searching Fandango autocomplete: %s", search_url)

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(search_url, headers={
                **BROWSER_HEADERS,
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.fandango.com/",
            })

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    movie_lower = movie_name.lower()

                    # Parse autocomplete results
                    movies = data if isinstance(data, list) else data.get("movies", [])
                    for m in movies:
                        m_name = m.get("name", "") or m.get("title", "")
                        m_year = str(m.get("year", ""))
                        if movie_lower in m_name.lower() and (not movie_year or m_year == movie_year):
                            m_url = m.get("url", "") or m.get("link", "")
                            if m_url and not m_url.startswith("http"):
                                m_url = f"https://www.fandango.com{m_url}"
                            # Rewrite /movie-overview to /movie-times with date
                            m_url = re.sub(r"/movie-overview$", f"/movie-times?date={target_date}", m_url)
                            if "/movie-times" not in m_url and m_url:
                                m_url = m_url.rstrip("/") + f"/movie-times?date={target_date}"
                            findings.append({
                                "source": "fandango_search",
                                "theatre_name": theatre.get("name", "Theatre"),
                                "movie_name": m_name,
                                "showtime": "Movie found — check for showtimes",
                                "show_date": target_date,
                                "ticket_url": m_url,
                            })
                except (json.JSONDecodeError, TypeError):
                    pass
    except Exception as exc:
        log.error("Fandango autocomplete error: %s", exc)

    # Also try the HTML search page
    if not findings:
        search_page_url = f"https://www.fandango.com/search?q={encoded}&mode=movies"
        log.info("Searching Fandango web: %s", search_page_url)

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(search_page_url, headers=BROWSER_HEADERS)
                if resp.status_code == 200:
                    movie_lower = movie_name.lower()
                    if movie_lower in resp.text.lower():
                        soup = BeautifulSoup(resp.text, "html.parser")

                        # Look for movie result links
                        result_links = soup.find_all("a", href=re.compile(r"/movie-overview|/movie-times"))
                        for link in result_links:
                            if movie_lower in link.get_text().lower():
                                href = link.get("href", "")
                                m_url = href if href.startswith("http") else f"https://www.fandango.com{href}"

                                # Check if it's the right year (avoid an older same-name release)
                                link_text = link.get_text().lower()
                                if movie_year and movie_year not in link_text and "2025" in link_text:
                                    continue

                                # Rewrite /movie-overview to /movie-times with date
                                m_url = re.sub(r"/movie-overview$", f"/movie-times?date={target_date}", m_url)

                                findings.append({
                                    "source": "fandango_search",
                                    "theatre_name": theatre.get("name", "Theatre"),
                                    "movie_name": movie_name,
                                    "showtime": "Movie found on Fandango",
                                    "show_date": target_date,
                                    "ticket_url": m_url,
                                })
                                break
        except Exception as exc:
            log.error("Fandango web search error: %s", exc)

    return findings


async def _fandango_theatre_page(theatre: dict, movie_name: str, target_date: str) -> list[dict]:
    """Check the Fandango theatre page for the movie's showtimes."""
    slug = theatre.get("slug", "")
    if not slug:
        return []

    url = f"https://www.fandango.com/{slug}/theater-page?date={target_date}"
    log.info("Checking Fandango theatre page: %s", url)

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code != 200:
                log.warning("Fandango theatre page returned %s for %s", resp.status_code, slug)
                return []

            return _parse_fandango_theatre_html(resp.text, movie_name, theatre, target_date, url)
    except Exception as exc:
        log.error("Fandango theatre page error: %s", exc)
        return []


def _parse_fandango_theatre_html(
    html: str, movie_name: str, theatre: dict, target_date: str, page_url: str
) -> list[dict]:
    """Parse Fandango theatre page for movie showtimes."""
    findings = []
    movie_lower = movie_name.lower()
    conf = cfg.get_config()
    evening_hour = int(conf.get("evening_start_hour", 16))
    movie_year = conf.get("movie_year", "2026")

    # Check if movie appears on this page at all
    if movie_lower not in html.lower():
        return []

    soup = BeautifulSoup(html, "html.parser")

    # Try to find embedded JSON data first
    scripts = soup.find_all("script", type=re.compile(r"application/(ld\+)?json"))
    for script in scripts:
        try:
            data = json.loads(script.string or "")
            if _json_contains_movie(data, movie_lower):
                return [{
                    "source": "fandango_web",
                    "theatre_name": theatre.get("name", "Theatre"),
                    "movie_name": movie_name,
                    "showtime": "Showtimes available",
                    "show_date": target_date,
                    "ticket_url": page_url,
                }]
        except (json.JSONDecodeError, TypeError):
            continue

    # Parse HTML for movie sections
    movie_items = soup.find_all(
        ["div", "li", "section", "article"],
        class_=re.compile(r"(movie|film|showtime|listing)", re.IGNORECASE),
    )

    for item in movie_items:
        item_text = item.get_text(" ", strip=True).lower()
        if movie_lower not in item_text:
            continue

        # Avoid matching 2025 version
        if "2025" in item_text and movie_year and movie_year != "2025":
            continue

        # Found it — look for times
        time_links = item.find_all(
            ["a", "button", "span"],
            class_=re.compile(r"(time|showtime|btn)", re.IGNORECASE),
        )

        for tl in time_links:
            time_text = tl.get_text(strip=True)
            href = tl.get("href", "")
            ticket_url = href if href.startswith("http") else f"https://www.fandango.com{href}" if href else page_url

            if _is_evening_time(time_text, evening_hour):
                findings.append({
                    "source": "fandango_web",
                    "theatre_name": theatre.get("name", "Theatre"),
                    "movie_name": movie_name,
                    "showtime": time_text,
                    "show_date": target_date,
                    "ticket_url": ticket_url,
                })

        if not time_links:
            findings.append({
                "source": "fandango_web",
                "theatre_name": theatre.get("name", "Theatre"),
                "movie_name": movie_name,
                "showtime": "Showtimes available (check site)",
                "show_date": target_date,
                "ticket_url": page_url,
            })

    # Last resort: movie name found in raw HTML
    if not findings:
        findings.append({
            "source": "fandango_web",
            "theatre_name": theatre.get("name", "Theatre"),
            "movie_name": movie_name,
            "showtime": "Detected on page (check site for details)",
            "show_date": target_date,
            "ticket_url": page_url,
        })

    return findings


# ===========================================================================
# Utilities
# ===========================================================================

def _element_context(link) -> str:
    """Collect text/attributes from a link and its nearby ancestors.

    Used to inspect a showtime button for sold-out state and format labels.
    """
    parts: list[str] = []

    def _attrs(el):
        parts.append(el.get_text(" ", strip=True))
        for attr in ("aria-label", "aria-describedby", "title", "data-status"):
            val = el.get(attr, "")
            if val:
                parts.append(val)
        cls = el.get("class", "")
        if isinstance(cls, list):
            parts.append(" ".join(cls))
        elif cls:
            parts.append(cls)

    _attrs(link)
    # Walk up a couple of levels — AMC wraps each showtime button in a
    # container that carries the "sold out" state and format label.
    parent = link.parent
    depth = 0
    while parent is not None and depth < 3 and parent.name not in ("body", "html", "main"):
        _attrs(parent)
        parent = parent.parent
        depth += 1

    return " ".join(p for p in parts if p)


def _is_sold_out(link) -> bool:
    """Return True if a showtime element looks sold out / unbookable."""
    return bool(SOLD_OUT_RE.search(_element_context(link)))


def _matches_format(context: str, movie_format: str) -> bool:
    """Check whether `context` advertises the required premium format.

    Empty movie_format means "any format is fine".
    """
    if not movie_format:
        return True
    ctx = context or ""
    ok = True
    if FORMAT_70MM_RE.search(movie_format):
        ok = ok and bool(FORMAT_70MM_RE.search(ctx))
    if IMAX_RE.search(movie_format):
        ok = ok and bool(IMAX_RE.search(ctx))
    return ok


# Extract a clean time token, and separately the availability status, from the
# text of a showtime button (which may also contain promos like "UP TO 15% OFF").
SHOWTIME_RE = re.compile(r"\d{1,2}:\d{2}\s*[ap]\.?m\.?", re.IGNORECASE)
STATUS_RE = re.compile(
    r"almost full|sold\s*out|few (?:seats|tickets) left|last chance|limited", re.IGNORECASE
)


def _clean_showtime(text: str) -> str:
    """Return just the time (e.g. '2:00pm') from a noisy button label."""
    m = SHOWTIME_RE.search(text or "")
    return m.group(0).replace(" ", "").replace(".", "").lower() if m else (text or "").strip()


def _status_from_text(text: str) -> str:
    """Return a normalised availability status ('Almost Full', ...) if present."""
    m = STATUS_RE.search(text or "")
    return m.group(0).title() if m else ""


def _is_evening_time(text: str, evening_hour: int = 16) -> bool:
    """Check if a time string represents an evening showtime."""
    text = text.strip().upper()

    # Match common time formats: "6:00 PM", "18:00", "6:00pm", etc.
    pm_match = re.search(r"(\d{1,2}):?(\d{2})?\s*(PM|AM)", text, re.IGNORECASE)
    if pm_match:
        hour = int(pm_match.group(1))
        ampm = pm_match.group(3).upper()
        if ampm == "PM" and hour != 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
        return hour >= evening_hour

    # 24-hour format
    h24_match = re.search(r"(\d{2}):(\d{2})", text)
    if h24_match:
        hour = int(h24_match.group(1))
        return hour >= evening_hour

    # If we can't parse, include it (better to over-report than miss)
    return True


# ===========================================================================
# Main check orchestrator
# ===========================================================================

async def run_all_checks() -> tuple[list[dict], list[str], str]:
    """Run checks across all configured theatres, sources, and dates.

    Sweeps every date in the configured range (start_date..end_date) so we
    catch open seats at any showtime over the coming weeks.

    Returns (findings, sources_checked, details_summary).
    """
    conf = cfg.get_config()
    movie_name = conf.get("movie_name", "The Odyssey")
    movie_format = conf.get("movie_format", "")
    dates = cfg.get_date_range()
    theatres = cfg.get_theatres()

    label = f"{movie_name}" + (f" ({movie_format})" if movie_format else "")
    log.info(
        "Sweeping %d date(s) %s..%s across %d theatre(s) for '%s'",
        len(dates), dates[0] if dates else "?", dates[-1] if dates else "?",
        len(theatres), label,
    )

    all_findings: list[dict] = []
    sources_checked: list[str] = []
    details_lines: list[str] = []

    # Bound concurrency so a wide date range doesn't hammer AMC (or spawn too
    # many browser contexts) all at once, while still finishing a cycle quickly.
    sem = asyncio.Semaphore(int(conf.get("max_concurrency", 4) or 4))

    for theatre in theatres:
        source = theatre.get("source", "amc")
        theatre_name = theatre.get("name", "Unknown")

        async def _check_date(target_date: str):
            async with sem:
                try:
                    if source == "amc":
                        findings = await check_amc(theatre, movie_name, target_date)
                    elif source == "fandango":
                        findings = await check_fandango(theatre, movie_name, target_date)
                    else:
                        log.warning("Unknown source: %s", source)
                        return None
                    for f in findings:
                        f.setdefault("show_date", target_date)
                    return findings
                except Exception as exc:
                    log.error("Error checking %s on %s: %s", theatre_name, target_date, exc)
                    return None  # sentinel for error

        results = await asyncio.gather(*[_check_date(d) for d in dates])

        theatre_findings: list[dict] = []
        errored = False
        for r in results:
            if r is None:
                errored = True
            else:
                theatre_findings.extend(r)

        suffix = " (partial error)" if errored else ""
        sources_checked.append(f"{source}:{theatre_name}{suffix}")
        all_findings.extend(theatre_findings)

        if theatre_findings:
            span = f"{dates[0]}→{dates[-1]}" if dates else ""
            details_lines.append(
                f"✅ {theatre_name}: {len(theatre_findings)} open showtime(s) "
                f"across {len(dates)} date(s) {span}".strip()
            )
            # Show only a short preview; full list is in /api/findings.
            preview = 5
            for f in theatre_findings[:preview]:
                status = f.get("status", "")
                status_str = f" ({status})" if status else ""
                details_lines.append(
                    f"   • {f.get('show_date', '')} {f.get('showtime', 'N/A')}{status_str}"
                )
            if len(theatre_findings) > preview:
                details_lines.append(
                    f"   … +{len(theatre_findings) - preview} more (see /api/findings)"
                )
        else:
            details_lines.append(
                f"❌ {theatre_name}: No open {movie_format or ''} seats across "
                f"{len(dates)} date(s)".replace("  ", " ")
            )

    details = "\n".join(details_lines)
    return all_findings, sources_checked, details
