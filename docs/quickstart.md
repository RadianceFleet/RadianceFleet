# Quick Start

## Prerequisites

- Docker + Docker Compose (for PostgreSQL/PostGIS)
- Python 3.12+
- `uv` package manager: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Node.js 18+ (for frontend)

## 1. Start the database

```bash
docker compose up -d db
```

Wait for the health check to pass:
```bash
docker compose ps   # STATUS should be "healthy"
```

## 2. Initialize the schema

```bash
cd backend
uv run radiancefleet init-db
```

## 3. Import seed corridors

```bash
uv run radiancefleet corridors import ../config/corridors.yaml
```

This imports 11 corridors: 4 export routes, 5 STS zones, 2 dark zones (GPS jamming areas).

## 4. Generate test vessels (optional)

Seven synthetic vessels covering all detection scenarios:

```bash
uv run python scripts/generate_sample_data.py
```

| Vessel | Scenario |
|--------|----------|
| A | 26-hour AIS gap on Baltic route |
| B | Circle spoofing pattern |
| C | Ship-to-ship transfer |
| D | OFAC watchlist hit |
| E | New MMSI (no history) |
| F | Clean — no anomalies |
| G | Impossible reappearance after gap |

## 5. Run the full detection pipeline

```bash
make detect
```

Or step by step:
```bash
uv run radiancefleet detect-gaps
uv run radiancefleet detect-spoofing
uv run radiancefleet detect-loitering
uv run radiancefleet detect-sts
uv run radiancefleet correlate-corridors
uv run radiancefleet score-alerts
```

## 6. View alerts

```bash
uv run radiancefleet list-alerts --min-score 20
```

Example output:
```
Alert #1  Score: 85  Vessel: 123456789  Duration: 26.2h  Status: new
Alert #3  Score: 62  Vessel: 555555555  Duration: 8.1h   Status: new
```

## 7. Start the web UI

```bash
# Terminal 1: API server
make serve         # or: cd backend && uv run radiancefleet serve

# Terminal 2: Frontend dev server
cd frontend && npm install && npm run dev
```

Open http://localhost:5173

## 8. Analyst workflow

1. Review alert queue — filter by score, status, vessel name
2. Click an alert to open the detail page
3. Check map: last known position (green), first position after gap (red), movement envelope (blue polygon)
4. Set status to `under_review`
5. Add analyst notes
6. Optionally prepare a satellite check: `uv run radiancefleet satellite prepare --alert 1`
7. Set status to `documented` or `dismissed`
8. Export evidence card

## 9. Export an evidence card

```bash
# Markdown (for reports)
uv run radiancefleet export evidence --alert 1 --format md

# Write to file
uv run radiancefleet export evidence --alert 1 --format md --output evidence_alert_1.md

# JSON (for downstream analysis)
uv run radiancefleet export evidence --alert 1 --format json
```

> Note: Export is blocked for alerts with status `new` — must set status to `under_review` or higher first (NFR7).

## 10. Import watchlists

```bash
# OFAC SDN list
uv run radiancefleet watchlist import --source ofac ./data/sdn.csv

# KSE shadow fleet list
uv run radiancefleet watchlist import --source kse ./data/kse_vessels.csv

# OpenSanctions
uv run radiancefleet watchlist import --source opensanctions ./data/opensanctions.csv
```

## 11. Import GFW detections (FR8)

Download pre-computed vessel detections from https://globalfishingwatch.org/data-download/ then:

```bash
uv run radiancefleet gfw import ./data/gfw_detections.csv
```

Unmatched detections (no AIS within ±2nm / ±3h) are stored as `DarkVesselDetection` records.

## Running tests

```bash
cd backend && uv run pytest tests/ -v
```
