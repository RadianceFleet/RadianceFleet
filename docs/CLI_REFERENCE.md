# CLI Reference

`radiancefleet` is the command-line interface for RadianceFleet. All commands are available after installing the package and activating the virtual environment.

```bash
source backend/.venv/bin/activate
radiancefleet --help
```

---

## Quick Setup

### `setup`

Bootstrap RadianceFleet from scratch with a single command. Initializes the database, seeds ports, imports corridors, optionally loads sample data, fetches watchlists, and runs the full detection pipeline.

```
radiancefleet setup [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--with-sample-data` | flag | off | Load 7 synthetic vessels for demo |
| `--skip-fetch` | flag | off | Skip downloading watchlists from the internet |

- Checks Python version (>= 3.11), verifies database connectivity.
- Runs `init-db`, imports corridors, optionally generates sample data.
- Downloads OFAC + OpenSanctions watchlists (unless `--skip-fetch`).
- Runs the full detection pipeline (gaps → spoofing → loitering → STS → corridors → score).
- Prints a summary with next-step instructions.

**Examples**:

```bash
# Full setup with sample data (recommended for first run)
radiancefleet setup --with-sample-data

# Setup without internet access
radiancefleet setup --with-sample-data --skip-fetch

# Setup for production (no sample data, fetch real watchlists)
radiancefleet setup
```

---

## Data Acquisition

### `data fetch`

Download watchlist data from public URLs. Uses conditional GET (ETag/Last-Modified) to avoid redundant downloads.

```
radiancefleet data fetch [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--source` | str | `all` | Source to fetch: `ofac`, `opensanctions`, or `all` |
| `--output-dir` | str | DATA_DIR setting | Download directory |
| `--force` | flag | off | Skip ETag check, always re-download |

**Examples**:

```bash
radiancefleet data fetch
radiancefleet data fetch --source ofac --force
```

---

### `data refresh`

One-command workflow: fetch latest watchlists → import → run detection pipeline.

```
radiancefleet data refresh [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--source` | str | `all` | Source to refresh: `ofac`, `opensanctions`, or `all` |
| `--detect/--no-detect` | flag | `--detect` | Run detection pipeline after import |

**Examples**:

```bash
# Full refresh (fetch + import + detect)
radiancefleet data refresh

# Just update watchlists without re-running detection
radiancefleet data refresh --no-detect
```

---

### `data status`

Show data freshness and record counts at a glance.

```
radiancefleet data status
```

Displays a table with source name, last import timestamp, and record count for: AIS positions, OFAC SDN, OpenSanctions, KSE shadow fleet, GFW detections, corridors, ports, and scored alerts.

**Example**:

```bash
radiancefleet data status
```

---

## Phase 1 — Setup

### `init-db`

Initialize the database schema. Also seeds the ports table if it is empty.

```
radiancefleet init-db
```

- Creates all tables defined in the SQLAlchemy models.
- Auto-seeds ~50 major global ports from `scripts/seed_ports.py` if the ports table is empty. Idempotent on subsequent runs.
- No flags.

**Example**:

```bash
radiancefleet init-db
```

---

### `seed-ports`

Seed the ports table with ~50 major global ports. Idempotent — ports already present are skipped.

```
radiancefleet seed-ports
```

- No flags.
- Prints counts of inserted and skipped ports.

**Example**:

```bash
radiancefleet seed-ports
```

---

### `serve`

Start the RadianceFleet FastAPI server with uvicorn.

```
radiancefleet serve [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--host` | str | `127.0.0.1` | Bind address |
| `--port` | int | `8000` | Bind port |
| `--reload` | flag | off | Enable hot-reload (development only) |

**Example**:

```bash
# Production-style local run
radiancefleet serve

# Development with auto-reload
radiancefleet serve --host 0.0.0.0 --port 8080 --reload
```

---

## Phase 2 — Ingestion

### `ingest ais`

Ingest AIS position records from a CSV file.

```
radiancefleet ingest ais <filepath>
```

| Argument | Description |
|----------|-------------|
| `filepath` | Path to the AIS CSV file (required) |

- Accepts DMA-format CSV and compatible exports.
- Deduplicates by (MMSI, timestamp) — safe to re-run on the same file.
- Prints accepted, rejected, and duplicate counts.
- First 10 parse errors are printed to the console.

**Example**:

```bash
radiancefleet ingest ais ./data/aisdk_2024_01.csv
```

---

### `gfw import`

Import pre-computed Global Fishing Watch vessel detection records.

```
radiancefleet gfw import <filepath>
```

| Argument | Description |
|----------|-------------|
| `filepath` | Path to the GFW vessel detections CSV (required) |

Expected CSV columns: `detect_id`, `timestamp`, `lat`, `lon`, `vessel_length_m`, `vessel_score`, `vessel_type`

Download from: https://globalfishingwatch.org/data-download/

- Rows with a matching AIS transmission are correlated as `matched`.
- Rows with no AIS match within the spatial-temporal window are stored as `dark` vessel detections.

**Example**:

```bash
radiancefleet gfw import ./data/gfw_detections_2024_q1.csv
```

---

### `watchlist import`

Import vessels from a sanctions or watchlist file.

```
radiancefleet watchlist import --source <source> <filepath>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--source` | str | yes | Source type: `ofac`, `kse`, or `opensanctions` |

| Argument | Description |
|----------|-------------|
| `filepath` | Path to the watchlist file (required) |

- `ofac` — OFAC SDN CSV (US Treasury). Processes rows where `SDN_TYPE == "Vessel"`.
- `kse` — KSE Institute shadow fleet CSV. Flexible column name detection.
- `opensanctions` — OpenSanctions JSON array of Vessel entities.

Vessel matching priority: MMSI exact match -> IMO exact match -> fuzzy name match at >= 85% confidence.

**Examples**:

```bash
radiancefleet watchlist import --source ofac ./data/sdn.csv
radiancefleet watchlist import --source kse ./data/kse_shadow_fleet.csv
radiancefleet watchlist import --source opensanctions ./data/opensanctions_vessels.json
```

---

### `corridors import`

Import or upsert corridors from a YAML file.

```
radiancefleet corridors import <filepath>
```

| Argument | Description |
|----------|-------------|
| `filepath` | Path to the corridors YAML file (required) |

- Upserts by corridor name — existing corridors are updated, new ones are inserted.
- Accepts optional GeoJSON-style `geometry` block for spatial queries.
- 11 seed corridors are shipped in `config/corridors.yaml` (4 export routes, 5 STS zones, 2 dark zones).

**Example**:

```bash
radiancefleet corridors import ./config/corridors.yaml
```

---

## Phase 3 — Detection

### `detect-gaps`

Run AIS gap detection. Identifies vessels that disappeared from AIS for an anomalous duration.

```
radiancefleet detect-gaps [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--from` | YYYY-MM-DD | none | Only process gaps starting on or after this date |
| `--to` | YYYY-MM-DD | none | Only process gaps starting before this date |

- Operates on all vessels with AIS data in the specified window, or all vessels if no dates given.
- Class B noise filter: gaps < 180 seconds are suppressed.
- Velocity ratio > 1.1 flags impossible reappearance speed.
- Outputs: gaps detected, vessels processed.

**Example**:

```bash
radiancefleet detect-gaps --from 2026-01-01 --to 2026-02-01
```

---

### `detect-spoofing`

Run AIS spoofing detection. Identifies five spoofing typologies: impossible speed, anchor-in-ocean, circle spoof, impossible reappearance, and stationary MMSI broadcast.

```
radiancefleet detect-spoofing [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--from` | YYYY-MM-DD | none | Start of analysis window |
| `--to` | YYYY-MM-DD | none | End of analysis window |

**Example**:

```bash
radiancefleet detect-spoofing --from 2026-01-01 --to 2026-02-01
```

---

### `detect-loitering`

Detect loitering events (vessels moving in slow circles or hovering in an area) and update laid-up vessel flags (30-day and 60-day).

```
radiancefleet detect-loitering [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--from` | YYYY-MM-DD | none | Start of analysis window |
| `--to` | YYYY-MM-DD | none | End of analysis window |

- Uses 1-hour SOG windows (Polars group_by_dynamic).
- Flags vessels as laid-up if loitering persists across 30 or 60 days.
- Links loiter-gap-loiter sequences for STS zone analysis.

**Example**:

```bash
radiancefleet detect-loitering --from 2026-01-01
```

---

### `detect-sts`

Detect ship-to-ship transfer events. Uses a two-phase algorithm: Phase A (proximity within 200m across 8+ time windows), Phase B (approaching vector analysis).

```
radiancefleet detect-sts [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--from` | YYYY-MM-DD | none | Start of analysis window |
| `--to` | YYYY-MM-DD | none | End of analysis window |

**Note**: Run `detect-gaps` before `detect-sts`. The STS scorer adds +15 points for one-vessel-dark-during-proximity, which requires gap records to be present.

**Example**:

```bash
radiancefleet detect-gaps --from 2026-01-01
radiancefleet detect-sts --from 2026-01-01
```

---

### `correlate-corridors`

Re-run ST_Intersects corridor correlation on all gap events not yet correlated with a corridor. Uses trajectory-based intersection (not endpoint-only).

```
radiancefleet correlate-corridors
```

- No flags.
- Marks gaps in dark zones (`is_jamming_zone: true` corridors). Dark zone gaps receive -10 on gap score.
- Outputs: correlated gap count, gaps in dark zones.

**Example**:

```bash
radiancefleet correlate-corridors
```

---

## Phase 4 — Scoring

### `score-alerts`

Score all unscored gap events using the risk scoring engine.

```
radiancefleet score-alerts
```

- No flags.
- Only processes gaps with `risk_score IS NULL`.
- Weights are loaded from `config/risk_scoring.yaml`.
- Outputs: count of scored alerts.

**Example**:

```bash
radiancefleet score-alerts
```

---

### `rescore-all-alerts`

Clear all existing risk scores and recompute from scratch. Use this after editing `config/risk_scoring.yaml`.

```
radiancefleet rescore-all-alerts
```

- No flags.
- Outputs: rescored count and config hash for NFR3 reproducibility.

**Example**:

```bash
# Edit weights, then rescore
nano config/risk_scoring.yaml
radiancefleet rescore-all-alerts
```

---

## Phase 5 — Triage

### `list-alerts`

List alerts in a rich table or CSV for terminal triage.

```
radiancefleet list-alerts [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--min-score` | int | none | Minimum risk score filter |
| `--status` | str | none | Filter by status: `new`, `under_review`, `confirmed`, `dismissed` |
| `--format` | str | `table` | Output format: `table` (rich colored) or `csv` (stdout) |
| `--limit` | int | `50` | Maximum number of alerts to return |

- Table output is color-coded: red for score >= 76 (critical), yellow for >= 51 (high).
- CSV output writes to stdout and can be piped or redirected.

**Examples**:

```bash
# Show top 20 unreviewed critical alerts
radiancefleet list-alerts --min-score 76 --status new --limit 20

# Export confirmed alerts to CSV
radiancefleet list-alerts --status confirmed --format csv > confirmed.csv
```

---

### `search`

Find a vessel by MMSI, IMO, or name and show its watchlist status and last known position.

```
radiancefleet search [OPTIONS]
```

| Flag | Type | Description |
|------|------|-------------|
| `--mmsi` | str | 9-digit MMSI |
| `--imo` | str | IMO number |
| `--name` | str | Vessel name (partial, case-insensitive) |

Exactly one of `--mmsi`, `--imo`, or `--name` must be provided.

**Examples**:

```bash
radiancefleet search --mmsi 273338710
radiancefleet search --imo 9284673
radiancefleet search --name "LUCKY STAR"
```

---

## Phase 6 — Satellite

### `satellite prepare`

Prepare a satellite check package for a specific alert. Computes the bounding box of the gap's movement envelope and generates a Copernicus Open Access Hub query URL.

```
radiancefleet satellite prepare --alert <alert_id>
```

| Flag | Type | Required | Description |
|------|------|----------|-------------|
| `--alert` | str | yes | Alert ID — accepts `ALERT_123` or just `123` |

- Outputs: satellite check ID, Copernicus URL, and bounding box coordinates.
- The gap must exist and have a movement envelope before this command is useful.

**Example**:

```bash
radiancefleet satellite prepare --alert 42
radiancefleet satellite prepare --alert ALERT_42
```

---

## Phase 7 — Export

### `export evidence`

Export an evidence card for an alert to Markdown or JSON.

```
radiancefleet export evidence --alert <alert_id> [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--alert` | int | required | Alert ID (gap_event_id) |
| `--format` | str | `md` | Output format: `md` (Markdown) or `json` |
| `--output` / `-o` | str | none | Write to file (default: stdout) |

**NFR7 gate**: The alert must not be in `new` status. Promote to `under_review`, `confirmed`, or `dismissed` via `list-alerts` + the API before exporting.

**Examples**:

```bash
# Print Markdown to terminal
radiancefleet export evidence --alert 42

# Write JSON to file
radiancefleet export evidence --alert 42 --format json --output ./reports/alert_42.json

# Markdown file
radiancefleet export evidence --alert 42 -o ./reports/alert_42.md
```

---

## Common Workflows

### Initial setup and first ingest

```bash
radiancefleet init-db
radiancefleet corridors import ./config/corridors.yaml
radiancefleet watchlist import --source kse ./data/kse_shadow_fleet.csv
radiancefleet ingest ais ./data/aisdk_2024_01.csv
```

### Full detection pipeline

```bash
radiancefleet detect-gaps --from 2026-01-01
radiancefleet detect-spoofing --from 2026-01-01
radiancefleet detect-loitering --from 2026-01-01
radiancefleet detect-sts --from 2026-01-01
radiancefleet correlate-corridors
radiancefleet score-alerts
```

### Triage and export

```bash
radiancefleet list-alerts --min-score 51 --status new
# Promote alerts via the API or UI, then:
radiancefleet export evidence --alert 42 --format json -o ./reports/alert_42.json
```
