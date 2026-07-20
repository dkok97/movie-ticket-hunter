# Pin to Debian 12 (bookworm). The unpinned python:3.12-slim now resolves to
# Debian 13 (trixie), which Playwright 1.49 doesn't recognize — it falls back
# to Ubuntu 20.04 package names (ttf-unifont, ttf-ubuntu-font-family) that don't
# exist on trixie, breaking `playwright install --with-deps`.
FROM python:3.12-slim-bookworm

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install the Chromium browser + OS deps for Playwright (headless fallback that
# gets past AMC's Queue-it waiting room). This is the heaviest layer.
RUN playwright install --with-deps chromium

# Copy application code
COPY . .

# Railway provides PORT env var; default to 8000
ENV PORT=8000

# Create a directory for the SQLite database (mount a Railway volume here)
RUN mkdir -p /data

EXPOSE ${PORT}

# Start the server (no --reload in production)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
