# ==============================================================================
# RAAH Production Containerfile
# Multi-stage minimal build for high-concurrency EMS dispatch & simulation
# ==============================================================================

FROM python:3.13-slim AS builder

WORKDIR /app

# Install build-essential if C-extensions are needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ==============================================================================
# FINAL RUNTIME IMAGE
# ==============================================================================
FROM python:3.13-slim

WORKDIR /app

# Create non-root user for security
RUN groupadd -r raah && useradd -r -g raah -d /app -s /sbin/nologin raah

# Copy installed dependencies from builder stage
COPY --from=builder /install /usr/local

# Copy application source code
COPY . /app

# Ensure data directories exist and are owned by raah user
RUN mkdir -p /app/data /app/data/optimization /app/data/scenarios /app/data/drills /app/data/replays \
    && chown -R raah:raah /app

USER raah

# Standard production environment defaults
ENV RAAH_ENVIRONMENT=production \
    RAAH_DEV_AUTH_FALLBACK=false \
    RAAH_CORS_ORIGINS="http://localhost:8000,http://127.0.0.1:8000" \
    RAAH_HOST=0.0.0.0 \
    RAAH_PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Container liveness health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live')" || exit 1

# Launch uvicorn production server
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
