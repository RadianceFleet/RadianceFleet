.PHONY: help setup dev serve dev-frontend test lint migrate docker-up docker-down generate-types

BACKEND_DIR := backend
FRONTEND_DIR := frontend

help:
	@echo "RadianceFleet — development commands"
	@echo ""
	@echo "  Setup"
	@echo "    make setup          Install all dependencies (backend + frontend)"
	@echo "    make docker-up      Start PostgreSQL + PostGIS"
	@echo "    make docker-down    Stop PostgreSQL"
	@echo ""
	@echo "  Development"
	@echo "    make dev            Start backend API server with hot-reload (port 8000)"
	@echo "    make serve          Alias for dev"
	@echo "    make dev-frontend   Start frontend dev server (port 5173)"
	@echo "    make test           Run backend tests"
	@echo "    make lint           Run ruff linter"
	@echo "    make migrate        Run Alembic migrations"
	@echo ""
	@echo "  CLI commands (run directly)"
	@echo "    radiancefleet start          First-time setup"
	@echo "    radiancefleet update         Daily data refresh + detection"
	@echo "    radiancefleet check-vessels  Review vessel identity issues"
	@echo "    radiancefleet open           Launch web dashboard"
	@echo "    radiancefleet status         System health check"
	@echo "    radiancefleet search         Vessel lookup by MMSI/IMO/name"

# ── Setup ─────────────────────────────────────────────────────────────────

setup:
	cd $(BACKEND_DIR) && uv sync
	cd $(FRONTEND_DIR) && npm install

docker-up:
	docker compose up -d
	@echo "Waiting for PostgreSQL to be ready..."
	@sleep 3
	@echo "PostgreSQL ready at localhost:5432"

docker-down:
	docker compose down

# ── Development ───────────────────────────────────────────────────────────

dev:
	cd $(BACKEND_DIR) && uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

serve: dev

dev-frontend:
	cd $(FRONTEND_DIR) && npm run dev

test:
	cd $(BACKEND_DIR) && uv run pytest tests/ -v --tb=short

lint:
	cd $(BACKEND_DIR) && uv run ruff check app/ tests/

migrate:
	cd $(BACKEND_DIR) && uv run alembic upgrade head

migrate-new:
	cd $(BACKEND_DIR) && uv run alembic revision --autogenerate -m "$(MSG)"

generate-types:
	cd $(FRONTEND_DIR) && npm run generate-types
