# 🎬 Odyssey IMAX 70mm Ticket Hunter

A bot + web dashboard that watches AMC for **open seats** to Christopher Nolan's
**The Odyssey (2026)** in the **IMAX 70mm** film format — the format that, in NYC,
only exists at **AMC Lincoln Square 13**.

The 70mm screenings are mostly sold out and run 4–5× per day (including overnight
2 a.m. shows) for several weeks. This bot re-checks **every 2 minutes**, sweeps the
**entire date range**, filters out sold-out showtimes, and sends an **instant push
notification** the moment a showtime has open seats.

> Fully configurable from the UI — point it at a different movie, format, theatre,
> or date range whenever you like.

---

## How it targets IMAX 70mm

- **AMC Lincoln Square 13** is the only NYC theatre capable of true IMAX 70mm, so it's
  the single configured theatre (`slug: amc-lincoln-square-13`, `region: new-york-city`).
- AMC publishes a **format-specific movie page** for the event —
  `the-odyssey-80679` ("The Odyssey – IMAX 70mm Event"), separate from the standard
  digital release (`the-odyssey-76238`). The bot watches the 70mm page, so every
  showtime it sees is already the right format.
- As a safety net, the `movie_format` setting (`IMAX 70mm`) is also matched against
  showtime context when scraping generic theatre pages, and **sold-out showtimes are
  filtered out** so you only get alerted for seats you can actually buy.
- `evening_start_hour` is set to `0` so the overnight 2 a.m. 70mm screenings are
  included rather than filtered as "not evening."

---

## Quick Start (Local)

### Prerequisites

- Python 3.12+
- Node.js 18+

### Backend

```bash
./backend/start.sh
```

Or manually:

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

The API server starts at **http://localhost:8000**.

### Frontend

```bash
./frontend/start.sh
```

Or manually:

```bash
cd frontend
npm install
npm run dev
```

The dashboard opens at **http://localhost:5173** (proxies API calls to the backend automatically).

---

## Notifications (ntfy.sh)

Notifications are powered by [ntfy.sh](https://ntfy.sh) — completely free, no signup required.

1. Install the **ntfy** app ([iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy))
2. Subscribe to the topic: **`odyssey-imax-70mm`**
3. Done — you'll get an instant push notification when open seats are found

When seats open at your priority theatre you'll get a **max-priority** alert with a
**BUY NOW** button that deep-links straight to the AMC seat-selection page. The bot
only alerts on **newly** available showtimes (it won't spam you every 2 minutes for
the same show), and it re-alerts if a sold-out show opens back up.

The topic name is configurable in the Settings tab.

---

## Configuration

All settings are editable from the **⚙️ Settings** tab in the UI:

| Setting | Default | Description |
|---|---|---|
| Movie Name | `The Odyssey` | Movie to search for |
| Movie Year | `2026` | Avoids matching older same-name releases |
| Format | `IMAX 70mm` | Premium-format filter applied to showtimes |
| Start Date | `2026-07-20` | First date in the sweep (inclusive) |
| End Date | `2026-08-10` | Last date in the sweep (inclusive, capped at 60 days) |
| Earliest Show Hour | `0` | `0` = report all shows incl. 2 a.m. screenings |
| Poll Interval | `2` minutes | How often the bot re-checks (min: 1) |
| AMC Movie Slug | `the-odyssey-80679` | The IMAX 70mm event page identifier |
| Priority Theatre | `AMC Lincoln Square 13` | Theatre highlighted with 1-click buy links |
| Number of Tickets | `2` | Shown in the purchase alert |
| ntfy Topic | `odyssey-imax-70mm` | Push notification channel |
| AMC API Key | _(empty)_ | Your `X-AMC-Vendor-Key` from [developers.amctheatres.com](https://developers.amctheatres.com). **Strongly recommended** — see below |
| Report Seats | `true` | Include open seat labels in notifications (needs API seat access) |
| Exclude Accessible Seats | `true` | Ignore wheelchair/companion seats when reporting/alerting |

### Data source: API vs scraping

The public AMC website sits behind a **Queue-it anti-bot waiting room**, so raw
HTTP scraping is unreliable (it returns the queue/challenge page, not showtimes).
When `AMC_API_KEY` is set, the bot uses the **AMC Developer API** as the
authoritative source — clean JSON, no Queue-it. Auth is a single
`X-AMC-Vendor-Key` header (set it as `AMC_API_KEY`); `AMC_VENDOR_ID` is no longer
required. The API path also reads each showtime's `isSoldOut` flag and premium
`attributes`, and (where the key permits seat-level access) lists the exact open
seats, excluding accessible/companion seats.

If the API is unavailable (no key, or the key isn't authorized yet), the bot
falls back to a **headless browser** (Playwright + Chromium) that renders the
AMC pages like a real browser — executing the client-side JS and passing the
Queue-it waiting room — then reuses the same parsers to find open IMAX 70mm
showtimes and read the seat map. This is enabled by default (`use_browser`) and
requires Chromium, which the `Dockerfile` installs via `playwright install
--with-deps chromium`. Set `PLAYWRIGHT_EXECUTABLE_PATH` to use a prebuilt
Chromium instead. The browser path is slower than the API, so a cycle checks
dates concurrently (`max_concurrency`, default 4).

### Default Theatre

- **AMC Lincoln Square 13** (via AMC) — the only NYC IMAX 70mm venue.

You can add/remove theatres from the Settings tab (name, source, url-slug, region, id).

---

## Environment Variables (optional)

Create a `backend/.env` file to set defaults:

```
AMC_API_KEY=your_x_amc_vendor_key   # from developers.amctheatres.com
NTFY_TOPIC=odyssey-imax-70mm
NTFY_SERVER=https://ntfy.sh
DATA_DIR=/data

# Only needed for auto_hold_seats when deployed to a datacenter host (see below)
HOLD_PROXY_SERVER=http://geo.iproyal.com:12321
HOLD_PROXY_USERNAME=your_proxy_username
HOLD_PROXY_PASSWORD=your_proxy_password

# Optional: IPRoyal-style sticky-session targeting appended to the password
# (defaults shown — tune to the theatre's actual city/state). Set
# HOLD_PROXY_STICKY=0 to disable if your provider isn't IPRoyal.
HOLD_PROXY_COUNTRY=us
HOLD_PROXY_STATE=newyork
HOLD_PROXY_CITY=newyorkcity
HOLD_PROXY_STICKY_LIFETIME=15m
```

### `auto_hold_seats` requires a residential proxy on cloud hosts

When `auto_hold_seats` is on, the bot drives AMC's guest checkout (seats →
tickets → order) to actually hold a seat instead of just linking to the
seat map. Confirmed via production debugging: AMC's CDN returns `403` for
every JS/CSS asset request when that checkout page is loaded from a
datacenter IP (Railway, AWS, GCP, etc.) — the page's HTML still loads, but
with zero interactivity, so the flow can never complete a hold. It works
fine from a normal residential/home IP.

To make this work on a cloud host, route just the hold flow's browser
traffic through a residential proxy by setting `HOLD_PROXY_SERVER` (+
`HOLD_PROXY_USERNAME`/`HOLD_PROXY_PASSWORD` if your provider requires auth).
Any residential-proxy provider that gives you an `http://host:port` +
credentials works (e.g. IPRoyal, Bright Data, Smartproxy). Only the hold
flow uses this proxy — the much higher-volume plain scraping/seat-checking
path never needs one, since it doesn't require JS to load.

**A plain rotating residential proxy isn't enough on its own.** Confirmed
via production debugging: IPRoyal's default rotating pool got hard-blocked
by Cloudflare's WAF outright (a `1020` firewall deny — worse than the
datacenter-IP case, which at least loaded the page). Two things fixed it:

1. **A sticky session per hold**, so the whole flow (document, every JS/CSS
   asset, every click) comes from *one* IP instead of potentially a
   different IP per request — which by itself looks exactly like a botnet.
2. **Geo-targeting near the theatre**, so the exit node looks like a genuine
   local resident instead of a random global IP from a heavily-abused pool.

`backend/purchase.py` builds this automatically for IPRoyal by appending
`_country-..._state-..._city-..._session-<random>_lifetime-...` to
`HOLD_PROXY_PASSWORD` (tunable via `HOLD_PROXY_COUNTRY`/`HOLD_PROXY_STATE`/
`HOLD_PROXY_CITY`/`HOLD_PROXY_STICKY_LIFETIME`, or disable with
`HOLD_PROXY_STICKY=0` for providers that use different syntax). See
[IPRoyal's docs](https://docs.iproyal.com/proxies/residential/proxy/rotation)
for the exact format if you're on a different provider.

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `GET` | `/api/status` | Bot status, schedule, date range, last run |
| `GET` | `/api/runs` | List of bot run history |
| `GET` | `/api/findings` | All ticket findings |
| `GET` | `/api/config` | Current configuration |
| `PUT` | `/api/config` | Update configuration |
| `POST` | `/api/trigger` | Trigger an immediate check |
| `POST` | `/api/test-notification` | Send a test push notification |
| `DELETE` | `/api/history` | Clear all run history and findings |

---

## How It Works

Each cycle (every 2 minutes) the bot:

1. **Sweeps the date range** — for every date from Start to End, it fetches the AMC
   IMAX 70mm event page for AMC Lincoln Square.
2. **Extracts bookable showtimes** — it collects the live seat-selection links
   (`/showtimes/{id}/seats`), which correspond to shows that still have seats.
3. **Filters** — drops anything marked *Sold Out* / unavailable, and (on generic
   pages) anything that isn't the IMAX 70mm format.
4. **De-dupes & notifies** — alerts only for showtimes that are *newly* available,
   via a high-priority ntfy push with a direct booking link.

With an `AMC_API_KEY` set, steps 1–3 are served by the AMC Developer API (one
JSON call per date, honoring `isSoldOut`/`attributes`), and step 2 additionally
fetches the seat map to list open, non-accessible seats. Without a key, the bot
falls back to scraping the (Queue-it-protected) website, which is far less
reliable — so the API key is strongly recommended.

## Architecture

```
┌──────────────────┐       ┌───────────────────────┐
│   Web dashboard   │──────▶│      Backend          │
│  React + Vite     │  API  │  FastAPI + APScheduler │
│                   │       │  SQLite                │
└──────────────────┘       └───────┬───────────────┘
                                   │  sweep date range
                                   ▼
                        AMC "The Odyssey – IMAX 70mm Event" page
                          (AMC Lincoln Square 13, all showtimes)
                                   │  open seats found
                                   ▼
                              ntfy.sh 📱
```
