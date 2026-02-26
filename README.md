# 🎬 Dhurandhar Ticket Hunter

A bot + web dashboard that monitors AMC and Fandango for movie ticket availability and sends instant push notifications when tickets drop.

Built to catch **Dhurandhar Revenge (2026)** tickets the moment they go on sale — but fully configurable for any movie.

---

## Quick Start (Local)

### Prerequisites

- Python 3.12+ (managed via pyenv)
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

## 🚀 Deployment (Railway + Vercel)

### 1. Push to GitHub

Make sure your repo is on GitHub (public or private).

### 2. Deploy Backend on Railway

1. Go to [railway.app](https://railway.app) and sign in
2. Click **New Project → Deploy from GitHub Repo**
3. Select your repo
4. Railway auto-detects the monorepo. Set the **Root Directory** to `backend`
5. Railway will build using the `Dockerfile` automatically

**Add a Volume** (for persistent SQLite database):
1. In the Railway service dashboard, click **+ New → Volume**
2. Set mount path to `/data`
3. Add an environment variable: `DATA_DIR=/data`

**Environment variables** to set in Railway:
| Variable | Value |
|---|---|
| `DATA_DIR` | `/data` |
| `NTFY_TOPIC` | `dhurandhar-tickets` (or your custom topic) |
| `AMC_API_KEY` | _(optional)_ |
| `AMC_VENDOR_ID` | _(optional)_ |

Railway will give you a public URL like `https://dhurandhar-backend-production-XXXX.up.railway.app`.

### 3. Deploy Frontend on Vercel

1. Go to [vercel.com](https://vercel.com) and sign in
2. Click **Add New → Project → Import Git Repository**
3. Select your repo
4. Set **Root Directory** to `frontend`
5. Framework Preset: **Vite** (should be auto-detected)
6. Add this **Environment Variable**:

| Variable | Value |
|---|---|
| `VITE_API_URL` | `https://your-railway-url.up.railway.app` |

> ⚠️ **No trailing slash** on the URL. Example: `https://dhurandhar-backend-production-abc123.up.railway.app`

7. Click **Deploy**

### 4. Verify

- Open your Vercel URL — you should see the dashboard
- The bot is running on Railway, checking every minute
- Install [ntfy.sh](https://ntfy.sh) and subscribe to your topic to receive notifications

---

## Notifications (ntfy.sh)

Notifications are powered by [ntfy.sh](https://ntfy.sh) — completely free, no signup required.

1. Install the **ntfy** app ([iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy))
2. Subscribe to the topic: **`dhurandhar-tickets`**
3. Done — you'll get instant push notifications when tickets are found

The topic name is configurable in the Settings tab.

---

## Configuration

All settings are editable from the **⚙️ Settings** tab in the UI:

| Setting | Default | Description |
|---|---|---|
| Movie Name | `Dhurandhar Revenge` | Movie to search for |
| Movie Year | `2026` | Avoids matching older releases |
| Target Date | `2026-03-19` | Date to check showtimes for |
| Poll Interval | `1` minute | How often the bot checks (min: 1) |
| AMC Movie Slug | `dhurandhar-the-revenge-83029` | Direct AMC movie page identifier |
| Evening Start Hour | `16` (4 PM) | Only report showtimes after this hour |
| ntfy Topic | `dhurandhar-tickets` | Push notification channel |
| AMC API Key | _(empty)_ | Optional — from [developers.amctheatres.com](https://developers.amctheatres.com) |

### Default Theatres

- AMC Empire 25 (via AMC)
- AMC Lincoln Square 13 (via AMC)
- AMC Kips Bay 15 (via AMC)
- Regal Union Square (via Fandango)

You can add/remove theatres from the Settings tab.

---

## Environment Variables (optional)

Create a `backend/.env` file to set defaults:

```
AMC_API_KEY=your_key_here
AMC_VENDOR_ID=your_vendor_id
NTFY_TOPIC=dhurandhar-tickets
NTFY_SERVER=https://ntfy.sh
DATA_DIR=/data
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | Health check (used by Railway) |
| `GET` | `/api/status` | Bot status, schedule info, last run |
| `GET` | `/api/runs` | List of bot run history |
| `GET` | `/api/findings` | All ticket findings |
| `GET` | `/api/config` | Current configuration |
| `PUT` | `/api/config` | Update configuration |
| `POST` | `/api/trigger` | Trigger an immediate check |
| `POST` | `/api/test-notification` | Send a test push notification |
| `DELETE` | `/api/history` | Clear all run history and findings |

---

## Architecture

```
┌──────────────────┐       ┌───────────────────────┐
│   Vercel (FE)    │──────▶│   Railway (Backend)   │
│  React + Vite    │  API  │  FastAPI + APScheduler │
│                  │       │  SQLite (volume)       │
└──────────────────┘       └───────┬───────────────┘
                                   │
                        ┌──────────┼──────────┐
                        ▼          ▼          ▼
                    AMC Web    AMC Direct   Fandango
                    Scraper    Movie Page   Scraper
                                   │
                                   ▼
                              ntfy.sh 📱
```

## How It Works

The bot uses multiple strategies per source to maximize detection:

**AMC** — Direct movie page (priority) → Developer API (if key provided) → showtime page scraping → search page  
**Fandango** — autocomplete API → search page scraping → theatre page scraping

Each check cycle iterates over all configured theatres, filters for evening shows, and stores results in a local SQLite database. When tickets are detected, an urgent push notification is sent via ntfy.sh with a direct booking link.
