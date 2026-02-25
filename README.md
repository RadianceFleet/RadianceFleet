# RadianceFleet

Open-source AIS anomaly detection for Russian shadow fleet triage.
For investigative journalists, OSINT researchers, and NGO analysts.

> ⚠ **Investigative triage tool — not a legal determination.**
> All exported evidence cards carry this disclaimer. Do not make enforcement
> or sanctions claims based solely on RadianceFleet output.

## What it does

- Detects AIS gaps on tanker routes in high-risk corridors (Baltic, Turkish Straits, Mediterranean, Persian Gulf)
- Scores risk with explainable, configurable weights (YAML) and outputs a 0–100 score per alert
- Flags spoofing patterns: anchor spoof, circle spoof, slow roll, MMSI reuse, nav status abuse
- Detects loitering (pre-STS behavior) and ship-to-ship transfers
- Cross-references OFAC SDN, KSE shadow fleet list, and OpenSanctions watchlists
- Prepares Sentinel-1 satellite check packages with pre-filled Copernicus Browser URLs
- Exports evidence cards (JSON + Markdown) with mandatory analyst disclaimer
- Imports GFW pre-computed vessel detections to identify dark ships (FR8)

## Quick start

See [docs/quickstart.md](docs/quickstart.md).

## Data coverage

AIS coverage varies significantly by region. See [docs/overclaiming-guide.md](docs/overclaiming-guide.md) before publishing.

| Region | Free AIS Quality |
|--------|-----------------|
| Baltic Sea | GOOD |
| Turkish Straits | GOOD |
| Mediterranean | MODERATE |
| Singapore Strait | PARTIAL |
| Far East / Nakhodka | PARTIAL |
| Black Sea | POOR |
| Persian Gulf | NONE |

## Configuration

- [docs/risk-scoring-config.md](docs/risk-scoring-config.md) — customize scoring weights
- [docs/corridor-config.md](docs/corridor-config.md) — add or edit monitored corridors

## Stack

| Layer | Technology |
|-------|-----------|
| Backend API | Python 3.12 + FastAPI + SQLAlchemy (sync) |
| Database | PostgreSQL + PostGIS (Docker) / SQLite + SpatiaLite (local dev) |
| CLI | Typer (`radiancefleet` entry point) |
| Package manager | `uv` |
| Frontend | React 18 + TypeScript + Vite |
| Map | React-Leaflet 4 |
| API client | TanStack Query v5 |

## License

Apache-2.0

---

*Built for journalists, not for courts.*
