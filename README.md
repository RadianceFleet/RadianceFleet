# RadianceFleet

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.12-blue)
![Tests](https://img.shields.io/badge/tests-321%20passing-brightgreen)

Open-source maritime anomaly detection for Russian shadow fleet triage.

RadianceFleet helps investigative journalists, OSINT researchers, and NGO analysts detect suspicious AIS gaps on oil tanker routes, score risk with explainable rules, and export evidence cards for publication. It is a triage and evidence assembly tool -- not a sanctions enforcement system or a legal determination engine.

> **Investigative triage tool -- not a legal finding.**
> All exported evidence cards carry a mandatory disclaimer.
> Read [docs/avoiding-overclaiming.md](docs/avoiding-overclaiming.md) before publishing.

## What It Does

- Ingests AIS position data from CSV files and normalizes records
- Detects AIS transmission gaps on tanker routes in high-risk corridors
- Identifies spoofing patterns: anchor spoof, circle spoof, slow roll, MMSI reuse, nav status abuse
- Detects loitering (pre-STS behavior) and ship-to-ship transfers
- Scores each alert 0-100 with explainable, configurable weights (YAML)
- Cross-references OFAC SDN, KSE shadow fleet list, and OpenSanctions watchlists
- Correlates gaps against 11 seed corridors (export routes, STS zones, dark zones)
- Prepares Sentinel-1 satellite check packages with pre-filled Copernicus Browser URLs
- Exports evidence cards (Markdown and JSON) with mandatory analyst disclaimer
- Imports Global Fishing Watch pre-computed vessel detections for dark ship identification
- Provides a vessel hunt workflow: target profiling, drift ellipse search missions, candidate scoring

## Quick Start

> **New to command-line tools?** See [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) for a guided walkthrough designed for journalists and analysts.

### Prerequisites

- Python 3.12+
- Node.js 18+ and npm (for the frontend)
- Docker and Docker Compose (for PostgreSQL + PostGIS), or SQLite + SpatiaLite for local-only mode
- [uv](https://docs.astral.sh/uv/) package manager

### Two-Command Setup

```bash
# 1. Start PostgreSQL + PostGIS
docker compose up -d

# 2. Install, initialize, and run everything
cd backend && uv sync && source .venv/bin/activate
radiancefleet setup --with-sample-data
```

This single command initializes the database, seeds ports, imports corridors, generates sample data (7 vessels), downloads watchlists, and runs the full detection pipeline.

```bash
# 3. Start the API server
radiancefleet serve

# 4. (Optional) Start the frontend
cd frontend && npm install && npm run dev
# Open http://localhost:5173
```

For a detailed walkthrough, see [docs/quickstart.md](docs/quickstart.md).

## Architecture

```
backend/              Python package
  app/models/         SQLAlchemy + GeoAlchemy2 data models
  app/modules/        Detection engines (gap, spoofing, loitering, STS, scoring, satellite)
  app/api/            FastAPI REST endpoints (38+)
  app/schemas/        Pydantic request/response schemas
  app/cli.py          Typer CLI entry point
  tests/              pytest (321 tests)
frontend/             React 18 + TypeScript + Vite + React-Leaflet
config/               corridors.yaml, risk_scoring.yaml
scripts/              Data generation and seeding utilities
```

| Layer | Technology |
|-------|-----------|
| Backend API | Python 3.12, FastAPI, SQLAlchemy (sync), GeoAlchemy2 |
| Database | PostgreSQL 16 + PostGIS 3.4 (Docker) / SQLite + SpatiaLite (local) |
| Data processing | Polars |
| CLI | Typer (`radiancefleet` entry point) |
| Package manager | uv |
| Frontend | React 18, TypeScript, Vite |
| Map | React-Leaflet 4 |
| API client | TanStack Query v5 |
| Watchlist matching | rapidfuzz (85% fuzzy threshold) |

## Key Features

- **AIS gap detection** -- class-aware speed thresholds, Class B noise filtering, rotated ellipse movement envelopes, velocity plausibility ratio
- **Spoofing detection** -- five typologies (anchor, circle, slow roll, MMSI reuse, nav status anomalies)
- **Loitering detection** -- 1h rolling SOG windows, laid-up vessel flags (30d/60d), loiter-gap-loiter pattern linking
- **STS transfer detection** -- 200m proximity over 2h sustained windows, heading alignment filter, corridor-aware scoring
- **Risk scoring engine** -- 12 signal categories, corridor and vessel-size multipliers, legitimacy deductions, gap frequency subsumption
- **Corridor correlation** -- ST_Intersects trajectory-based matching against 11 seed corridors
- **Watchlist matching** -- OFAC SDN, KSE shadow fleet, OpenSanctions with fuzzy name matching
- **Satellite workflow** -- bounding box generation, Copernicus Browser URL pre-fill, data source coverage metadata
- **Evidence export** -- Markdown and JSON cards with score breakdown, mandatory disclaimer, analyst review gate
- **Vessel hunt** -- target profiling, drift ellipse search missions, dark vessel candidate scoring and confirmation

## CLI Commands

```
# Setup & Data
radiancefleet setup [--with-sample-data] [--skip-fetch]
                                               One-command bootstrap (DB + corridors + data + detect)
radiancefleet data fetch [--source] [--force]  Download OFAC + OpenSanctions watchlists
radiancefleet data refresh [--no-detect]       Fetch → import → detect → score
radiancefleet data status                      Show data freshness and record counts

# Ingestion
radiancefleet ingest ais <file>                Ingest AIS records from CSV
radiancefleet init-db                          Initialize database and seed ports
radiancefleet seed-ports                       Seed ports table (idempotent)
radiancefleet corridors import <file>          Import corridors from YAML
radiancefleet watchlist import --source <type> <file>
                                               Import watchlist (ofac, kse, opensanctions)
radiancefleet gfw import <file>                Import GFW vessel detections

# Detection
radiancefleet detect-gaps [--from] [--to]      Run AIS gap detection
radiancefleet detect-spoofing [--from] [--to]  Run spoofing detection
radiancefleet detect-loitering [--from] [--to] Detect loitering and update laid-up flags
radiancefleet detect-sts [--from] [--to]       Detect ship-to-ship transfer events
radiancefleet correlate-corridors              Run ST_Intersects corridor correlation
radiancefleet score-alerts                     Score all unscored gap events
radiancefleet rescore-all-alerts               Clear and recompute all risk scores

# Triage & Export
radiancefleet list-alerts [--min-score] [--status] [--format table|csv]
radiancefleet search [--mmsi] [--imo] [--name] Find vessel and show watchlist status
radiancefleet satellite prepare --alert <id>   Prepare satellite check package
radiancefleet export evidence --alert <id> [--format md|json]
radiancefleet export gov-package --alert <id>  Export government alert package

# Vessel Hunt (FR9)
radiancefleet hunt create-target --vessel <id> Register vessel as hunt target
radiancefleet hunt create-mission --target <id> --from <date> --to <date>
radiancefleet hunt find-candidates --mission <id>
radiancefleet hunt list-missions [--status]
radiancefleet hunt confirm --mission <id> --candidate <id>

# Server
radiancefleet serve [--host] [--port] [--reload]
```

## Configuration

- **config/corridors.yaml** -- 11 seed corridors: 4 Russian export routes, 5 STS zones, 2 dark zones (GPS jamming). Add custom corridors or adjust risk weights.
- **config/risk_scoring.yaml** -- all scoring weights, score bands, detection thresholds. See [docs/risk-scoring-config.md](docs/risk-scoring-config.md).

Score bands (from `risk_scoring.yaml`):

| Band | Score | Meaning |
|------|-------|---------|
| Low | 0--20 | No action needed |
| Medium | 21--50 | Investigate; check satellite data |
| High | 51--75 | High confidence anomaly; publication-ready with analyst review |
| Critical | 76+ | Strong shadow fleet indicators; escalate |

## Data Coverage

AIS coverage varies significantly by region. See [docs/coverage-limitations.md](docs/coverage-limitations.md).

| Region | Free AIS Quality |
|--------|-----------------|
| Baltic Sea | GOOD |
| Turkish Straits | GOOD |
| Mediterranean | MODERATE |
| Singapore Strait | PARTIAL |
| Far East / Nakhodka | PARTIAL |
| Black Sea | POOR |
| Persian Gulf | NONE |

## Documentation

- [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) -- non-technical guide for journalists and analysts
- [docs/quickstart.md](docs/quickstart.md) -- developer walkthrough with sample data
- [docs/API_INTEGRATION.md](docs/API_INTEGRATION.md) -- API integration guide with runnable examples
- [docs/API.md](docs/API.md) -- complete REST API endpoint reference
- [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md) -- CLI command reference
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) -- data source import guide (with auto-download)
- [docs/avoiding-overclaiming.md](docs/avoiding-overclaiming.md) -- required reading before publication (NFR7)
- [docs/coverage-limitations.md](docs/coverage-limitations.md) -- AIS coverage gaps and dark zones
- [docs/risk-scoring-config.md](docs/risk-scoring-config.md) -- customize scoring weights
- [docs/corridor-config.md](docs/corridor-config.md) -- add or edit monitored corridors
- [docs/evidence-card-schema.md](docs/evidence-card-schema.md) -- evidence card format

## License

Apache-2.0. See [LICENSE](LICENSE).

---

*Built for journalists, not for courts.*
