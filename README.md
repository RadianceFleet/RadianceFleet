# RadianceFleet

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.12-blue)
![Tests](https://img.shields.io/badge/tests-115%20passing-brightgreen)

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

## Quick start in 3 commands

```bash
docker compose up -d && cd backend && uv sync && source .venv/bin/activate
radiancefleet init-db && radiancefleet corridors import config/corridors.yaml
radiancefleet ingest ais <your_ais_file.csv> && radiancefleet score-alerts
```

Then open http://localhost:5173, go to AlertList, and filter by score >= 50.

For a full walkthrough including the detection pipeline and analyst workflow, see [docs/quickstart.md](docs/quickstart.md).

## Screenshot

*(Screenshot coming after UI polish — see [docs/quickstart.md](docs/quickstart.md) for a step-by-step walkthrough)*

## Used for investigating

RadianceFleet is designed to support investigative journalism and open-source
intelligence research into Russian oil export evasion. Typical use cases include:

- Identifying tankers with unexplained AIS gaps on routes out of Primorsk,
  Novorossiysk, or Nakhodka
- Finding vessels that appear in the KSE shadow fleet list or OFAC SDN list
  and are still actively transiting monitored corridors
- Documenting ship-to-ship transfers in the Mediterranean and Gibraltar Strait
- Building evidence packages for publication that include satellite imagery
  cross-checks and mandatory analyst disclaimers

**Before publishing any findings, read [docs/overclaiming-guide.md](docs/overclaiming-guide.md).**
A high risk score is a signal to investigate further — not a finding in itself.
AIS gaps have many innocent explanations (equipment failure, GPS jamming, poor
coverage at sea). All exported evidence cards carry a mandatory disclaimer.
Consult a maritime law expert before making sanctions or legal claims.

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
