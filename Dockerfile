# syntax=docker/dockerfile:1  # Enables BuildKit features (cache mounts, etc.)

# ─────────────────────────────────────────────────────────────────────────────
# Base image - slim variant keeps it small
FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ── Install system dependencies ─────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# ── Create non-root user early ──────────────────────────────────────────────
RUN useradd -m -u 1000 appuser

# ── Set working directory ───────────────────────────────────────────────────
WORKDIR /app

# ── Install Python dependencies with caching ────────────────────────────────
COPY requirements.txt .

# Upgrade pip + install requirements (use cache mount for speed)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install --no-cache-dir --default-timeout=100 -r requirements.txt

    # Optional: faster mirror if you have connectivity issues again
    # pip install --no-cache-dir --default-timeout=100 \
    #     -i https://pypi.tuna.tsinghua.edu.cn/simple \
    #     -r requirements.txt

# ── Copy application code ───────────────────────────────────────────────────
COPY . .

# ── Prepare directories & permissions BEFORE switching user ─────────────────
RUN mkdir -p \
        /app/data/raw_docs \
        /app/data/cache_store \
        /app/staticfiles \
    && chown -R appuser:appuser /app

# ── Switch to non-root user ─────────────────────────────────────────────────
USER appuser

# ── Collect static files (requires STATIC_ROOT set in settings.py) ──────────
RUN python manage.py collectstatic --noinput --clear --verbosity 2

# ── Expose port ─────────────────────────────────────────────────────────────
EXPOSE 8000

# ── Healthcheck (better for Daphne/ASGI) ────────────────────────────────────
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl --fail http://localhost:8000/health/ || exit 1

    # Alternative simple version if you don't have a /health/ endpoint yet:
    # CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

# ── Start Daphne ASGI server ────────────────────────────────────────────────
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "Furniture_chatbot.asgi:application"]