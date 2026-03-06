# === Stage 1: Build frontend ===
FROM node:20-slim AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# === Stage 2: Backend ===
FROM ghcr.io/astral-sh/uv:0.6.6-python3.12-bookworm-slim

WORKDIR /app/backend

# Install dependencies first (cached layer)
COPY backend/pyproject.toml ./
RUN uv sync --no-dev

# Copy backend source
COPY backend/ ./

# Copy built frontend into static directory
COPY --from=frontend-build /app/frontend/dist ./static

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
