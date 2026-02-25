.PHONY: help setup dev test lint migrate docker-up docker-down ingest detect-gaps score

BACKEND_DIR := backend
FRONTEND_DIR := frontend

help:
	@echo "RadianceFleet — development commands"
	@echo ""
	@echo "  make setup        Install all dependencies (backend + frontend)"
	@echo "  make docker-up    Start PostgreSQL + PostGIS"
	@echo "  make docker-down  Stop PostgreSQL"
	@echo "  make init-db      Create database schema"
	@echo "  make migrate      Run Alembic migrations"
	@echo "  make dev          Start backend API server (port 8000)"
	@echo "  make dev-frontend Start frontend dev server (port 5173)"
	@echo "  make test         Run backend tests"
	@echo "  make lint         Run ruff linter"
	@echo "  make ingest       Ingest sample AIS data (requires data/sample.csv)"
	@echo "  make detect-gaps  Run gap detection"
	@echo "  make score        Score all alerts"

setup:
	cd $(BACKEND_DIR) && pip install -e ".[dev]"
	cd $(FRONTEND_DIR) && npm install

docker-up:
	docker compose up -d postgres
	@echo "Waiting for PostgreSQL to be ready..."
	@sleep 3
	@echo "PostgreSQL ready at localhost:5432"

docker-down:
	docker compose down

init-db:
	cd $(BACKEND_DIR) && python -c "from app.database import init_db; init_db()"

migrate:
	cd $(BACKEND_DIR) && alembic upgrade head

migrate-new:
	cd $(BACKEND_DIR) && alembic revision --autogenerate -m "$(MSG)"

dev:
	cd $(BACKEND_DIR) && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

dev-frontend:
	cd $(FRONTEND_DIR) && npm run dev

test:
	cd $(BACKEND_DIR) && pytest tests/ -v --tb=short

lint:
	cd $(BACKEND_DIR) && ruff check app/ tests/

generate-types:
	cd $(FRONTEND_DIR) && npm run generate-types

ingest:
	cd $(BACKEND_DIR) && radiancefleet ingest ais ../data/sample.csv

detect-gaps:
	cd $(BACKEND_DIR) && radiancefleet detect-gaps

detect-spoofing:
	cd $(BACKEND_DIR) && radiancefleet detect-spoofing

score:
	cd $(BACKEND_DIR) && radiancefleet score-alerts
