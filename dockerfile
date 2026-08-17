# syntax=docker/dockerfile:1
# ── Build stage: install dependencies ──────────────────────────────────────────
FROM python:3.12-slim AS builder

# Install uv — fast Python package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy only dependency manifests first to maximise Docker layer caching.
# Production deps only (no --dev / test tooling in the image).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /app/.venv /app/.venv

# Copy source
COPY . .

# Install the project itself so src.* imports resolve correctly
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Lightweight liveness check — hits /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

CMD ["uv", "run", "uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]