.PHONY: help setup dev serve dev-frontend test lint migrate docker-up docker-down \
       init-db ingest detect-gaps detect-spoofing detect-loitering detect-sts \
       correlate-corridors score detect fetch-data refresh data-status generate-types

BACKEND_DIR := backend
FRONTEND_DIR := frontend

help:
	@echo "RadianceFleet — development commands"
	@echo ""
	@echo "  Setup"
	@echo "    make setup          Install all dependencies (backend + frontend)"
	@echo "    make docker-up      Start PostgreSQL + PostGIS"
	@echo "    make docker-down    Stop PostgreSQL"
	@echo "    make init-db        Create database schema and seed ports"
	@echo ""
	@echo "  Development"
	@echo "    make dev            Start backend API server (port 8000)"
	@echo "    make serve          Alias for dev"
	@echo "    make dev-frontend   Start frontend dev server (port 5173)"
	@echo "    make test           Run backend tests"
	@echo "    make lint           Run ruff linter"
	@echo "    make migrate        Run Alembic migrations"
	@echo ""
	@echo "  Data"
	@echo "    make fetch-data     Download OFAC + OpenSanctions watchlists"
	@echo "    make refresh        Fetch, import, and run full detection pipeline"
	@echo "    make data-status    Show data freshness and record counts"
	@echo "    make ingest         Ingest sample AIS data (requires scripts/sample_ais.csv)"
	@echo ""
	@echo "  Detection"
	@echo "    make detect         Run the full detection pipeline (gaps → score)"
	@echo "    make detect-gaps    Run gap detection only"
	@echo "    make detect-spoofing Run spoofing detection only"
	@echo "    make detect-loitering Run loitering detection only"
	@echo "    make detect-sts     Run STS detection only"
	@echo "    make correlate-corridors Correlate gaps with corridors"
	@echo "    make score          Score all unscored alerts"

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

init-db:
	cd $(BACKEND_DIR) && uv run radiancefleet init-db

# ── Development ───────────────────────────────────────────────────────────

dev:
	cd $(BACKEND_DIR) && uv run radiancefleet serve --reload

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

# ── Data ──────────────────────────────────────────────────────────────────

fetch-data:
	cd $(BACKEND_DIR) && uv run radiancefleet data fetch

refresh:
	cd $(BACKEND_DIR) && uv run radiancefleet data refresh

data-status:
	cd $(BACKEND_DIR) && uv run radiancefleet data status

ingest:
	cd $(BACKEND_DIR) && uv run radiancefleet ingest ais scripts/sample_ais.csv

# ── Detection pipeline ────────────────────────────────────────────────────

detect: detect-gaps detect-spoofing detect-loitering detect-sts correlate-corridors score

detect-gaps:
	cd $(BACKEND_DIR) && uv run radiancefleet detect-gaps

detect-spoofing:
	cd $(BACKEND_DIR) && uv run radiancefleet detect-spoofing

detect-loitering:
	cd $(BACKEND_DIR) && uv run radiancefleet detect-loitering

detect-sts:
	cd $(BACKEND_DIR) && uv run radiancefleet detect-sts

correlate-corridors:
	cd $(BACKEND_DIR) && uv run radiancefleet correlate-corridors

score:
	cd $(BACKEND_DIR) && uv run radiancefleet score-alerts
