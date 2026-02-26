FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Railway provides PORT env var; default to 8000
ENV PORT=8000

# Create a directory for the SQLite database (mount a Railway volume here)
RUN mkdir -p /data

EXPOSE ${PORT}

# Start the server (no --reload in production)
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT}
