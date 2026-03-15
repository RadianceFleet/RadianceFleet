# === Stage 1: Build frontend ===
FROM node:20-slim AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# === Stage 2: Backend ===
FROM ghcr.io/astral-sh/uv:0.6.6-python3.12-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# Install dependencies first (cached layer — only re-runs if deps change)
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --no-dev --no-install-project

# Copy backend source + re-sync to install the project itself (fast — deps cached)
COPY backend/ ./
RUN uv sync --no-dev

# Copy config files (risk scoring, corridors, coverage zones)
COPY config/ ./config/

# Copy built frontend from Vite output (outDir: ../backend/static)
COPY --from=frontend-build /app/backend/static ./static

EXPOSE 8000

RUN adduser --disabled-password --gecos '' --no-create-home appuser
USER appuser

CMD ["bash", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
