# RadianceFleet Quick Reference

## Setup (first time)

```bash
git clone https://github.com/your-org/RadianceFleet.git && cd RadianceFleet
docker compose up -d                              # Start PostgreSQL + PostGIS
cd backend && uv sync && source .venv/bin/activate
radiancefleet init-db
radiancefleet corridors import config/corridors.yaml
```

## Daily workflow

```bash
# 1. Import AIS data
radiancefleet ingest ais <file.csv>

# 2. Run the detection pipeline
radiancefleet detect-gaps                         # Find AIS gaps (gap_detector)
radiancefleet detect-spoofing                     # Detect spoofing typologies
radiancefleet detect-loitering                    # Detect loitering / laid-up
radiancefleet detect-sts                          # Detect ship-to-ship transfers
radiancefleet correlate-corridors                 # Link events to corridors
radiancefleet score-alerts                        # Compute 0–100 risk scores
```

## Triage

```bash
# Web UI — alert list, map view, status management
open http://localhost:5173

# CLI triage
radiancefleet list-alerts --min-score 50          # High-risk alerts only
radiancefleet list-alerts --min-score 20 --limit 50  # Broader review queue
radiancefleet search --mmsi 123456789             # Vessel lookup by MMSI
radiancefleet search --name "VESSEL NAME"         # Vessel lookup by name
```

## Score bands

| Score | Band | Action |
|-------|------|--------|
| 0–20 | Low | No action needed |
| 21–50 | Medium | Investigate — check satellite data |
| 51–75 | High | Publication-ready with analyst review |
| 76+ | Critical | Strong shadow fleet indicators — escalate |

## Status workflow

```
new → under_review → documented → confirmed
                  └→ dismissed
```

Alert status must be `under_review` or higher before evidence can be exported.

## Export

```bash
# Evidence card — Markdown (for reports)
radiancefleet export evidence --alert <id> --format md

# Evidence card — write to file
radiancefleet export evidence --alert <id> --format md --output evidence_<id>.md

# Evidence card — JSON (for downstream analysis)
radiancefleet export evidence --alert <id> --format json

# Bulk CSV of all alerts via API
curl http://localhost:8000/api/v1/alerts/export > alerts.csv
```

## Satellite check preparation

```bash
radiancefleet satellite prepare --alert <id>
# Outputs a pre-filled Copernicus Browser URL covering the gap bounding box
```

## Watchlist management

```bash
radiancefleet watchlist import --source ofac ./data/sdn.csv
radiancefleet watchlist import --source kse ./data/kse_vessels.csv
radiancefleet watchlist import --source opensanctions ./data/opensanctions.csv
```

## Corridor management

```bash
radiancefleet corridors import config/corridors.yaml  # Import / refresh all corridors
curl http://localhost:8000/api/v1/corridors            # List corridors via API
```

## Config tuning

```bash
# After editing config/risk_scoring.yaml or config/corridors.yaml:
radiancefleet rescore-all-alerts

# Reproduce historical scores (NFR3):
radiancefleet rescore-all-alerts --scoring-date 2024-06-01
```

See [docs/CUSTOMIZATION.md](docs/CUSTOMIZATION.md) for scenario-based tuning examples.

## GFW dark vessel import

```bash
radiancefleet gfw import ./data/gfw_detections.csv
```

## Health check

```bash
curl http://localhost:8000/api/v1/health
docker compose ps                                 # Check container status
```

## Tests

```bash
cd backend && .venv/bin/pytest tests/ -v
```

## Useful API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/health` | Health check |
| GET | `/api/v1/alerts` | Alert list (filterable by score, status) |
| GET | `/api/v1/alerts/{id}` | Alert detail with breakdown |
| PATCH | `/api/v1/alerts/{id}/status` | Update alert status |
| GET | `/api/v1/alerts/export` | Bulk CSV export |
| GET | `/api/v1/vessels` | Vessel search |
| GET | `/api/v1/vessels/{mmsi}` | Vessel detail |
| GET | `/api/v1/corridors` | Corridor list |
| GET | `/api/v1/stats` | Dataset statistics |

Full API docs: http://localhost:8000/docs (Swagger UI when server is running)
