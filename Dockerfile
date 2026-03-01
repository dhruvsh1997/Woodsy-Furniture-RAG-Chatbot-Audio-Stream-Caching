# ── Stage 1: Python dependencies ──────────────────────────────────────────
FROM python:3.11-slim AS base

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create app user (non-root)
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install Python packages first (Docker cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Create data dirs with correct ownership
RUN mkdir -p data/raw_docs data/cache_store staticfiles \
    && chown -R appuser:appuser /app

USER appuser

# Collect static files at build time
RUN python manage.py collectstatic --noinput

EXPOSE 8000

# Health check — waits for Daphne to be ready
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

# Start Daphne ASGI server
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "Furniture_chatbot.asgi:application"]
