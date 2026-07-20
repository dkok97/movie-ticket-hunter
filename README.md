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
seats, excluding accessible/companion seats. If the API errors, the bot falls
back to web scraping automatically.

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
```

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
