# Contributing to RadianceFleet

Thank you for your interest in contributing. RadianceFleet is an open source
maritime anomaly detection tool for journalists, OSINT researchers, and NGO
analysts tracking the Russian shadow fleet. Contributions that improve detection
accuracy, data source coverage, or analyst ergonomics are especially welcome.

Please read this document before opening a pull request. All contributors are
expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Table of Contents

1. [Development environment setup](#1-development-environment-setup)
2. [Install dependencies](#2-install-dependencies)
3. [Run the test suite](#3-run-the-test-suite)
4. [Linting and formatting](#4-linting-and-formatting)
5. [Branch naming](#5-branch-naming)
6. [Pull request process](#6-pull-request-process)
7. [How to add a new detection signal](#7-how-to-add-a-new-detection-signal)
8. [How to add a new data source adapter](#8-how-to-add-a-new-data-source-adapter)
9. [Project layout reference](#9-project-layout-reference)

---

## 1. Development environment setup

### Prerequisites

| Tool | Minimum version | Notes |
|------|----------------|-------|
| Python | 3.12 | 3.11 also works; 3.12 is recommended |
| [uv](https://github.com/astral-sh/uv) | 0.4+ | Used for all Python dependency management |
| Docker + Docker Compose | any recent | For PostgreSQL + PostGIS |
| Node.js | 20+ | Frontend only |

### Clone and enter the repo

```bash
git clone https://github.com/your-org/RadianceFleet.git
cd RadianceFleet
```

### Database: PostgreSQL via Docker (recommended)

```bash
docker compose up -d          # starts postgres + postgis on port 5432
```

The default `DATABASE_URL` in `.env.example` points at the Docker instance.
Copy it and adjust as needed:

```bash
cp backend/.env.example backend/.env
```

### Database: SQLite + SpatiaLite (local dev, no Docker)

For lightweight local development without Docker, set `DATABASE_URL` to a
SpatiaLite path:

```bash
# backend/.env
DATABASE_URL=sqlite+pysqlite:///./radiancefleet.db
```

SpatiaLite must be installed on the host (`libsqlite3-mod-spatialite` on
Debian/Ubuntu). The application detects the driver and loads the extension
automatically on startup.

### Activate the virtual environment

```bash
source backend/.venv/bin/activate
```

The venv is created and managed by `uv`. Do not replace it with a plain
`python -m venv` environment.

---

## 2. Install dependencies

```bash
cd backend && uv sync
```

This installs both runtime and development extras (pytest, ruff, mypy,
types-pyyaml) as declared in `backend/pyproject.toml`.

To install only runtime dependencies (e.g. in CI):

```bash
cd backend && uv sync --no-dev
```

### Frontend

```bash
cd frontend && npm install
```

---

## 3. Run the test suite

All 85+ tests must pass before a PR is merged.

```bash
cd backend && .venv/bin/pytest tests/ -v
```

Run a single test file during development:

```bash
cd backend && .venv/bin/pytest tests/test_risk_scoring_complete.py -v
```

Run with coverage:

```bash
cd backend && .venv/bin/pytest tests/ -v --cov=app --cov-report=term-missing
```

Tests are unit-level by design. The `_make_gap()` factory in
`tests/test_risk_scoring_complete.py` constructs `MagicMock`-based
`AISGapEvent` objects so that the scoring engine can be tested without a live
database.

---

## 4. Linting and formatting

We use [ruff](https://docs.astral.sh/ruff/) for both linting and import
sorting. Configuration lives in `backend/pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]
```

Run the linter:

```bash
ruff check backend/
```

Auto-fix safe issues:

```bash
ruff check backend/ --fix
```

The CI pipeline runs `ruff check backend/` and will fail on any violations.
Please run it locally before pushing.

Optional static type checking:

```bash
cd backend && .venv/bin/mypy app/
```

mypy is configured in `strict = false` mode; new code should not introduce
`Any` annotations where avoidable, but this is not enforced as a CI gate for
now.

---

## 5. Branch naming

Use these prefixes so that CI workflows and changelogs stay tidy:

| Prefix | When to use |
|--------|-------------|
| `feat/` | New feature or detection capability |
| `fix/` | Bug fix |
| `docs/` | Documentation-only changes |
| `refactor/` | Internal restructuring with no user-visible change |
| `test/` | Tests only |
| `chore/` | Dependency bumps, CI config, tooling |

Examples: `feat/imo-cross-check`, `fix/dark-zone-scoring`, `docs/corridor-yaml`

---

## 6. Pull request process

1. Fork the repo and create a branch from `main` using the naming convention
   above.
2. Make sure `cd backend && .venv/bin/pytest tests/ -v` passes with no
   failures.
3. Run `ruff check backend/` and fix any violations.
4. If your change adds or modifies a CLI command or API endpoint, update the
   relevant section of `PRD.md` or the inline docstring. New CLI commands must
   appear in the `radiancefleet --help` output.
5. Open the PR against `main`. Fill in the template:
   - **What**: one-sentence summary of the change.
   - **Why**: link to an issue or describe the motivation.
   - **Test plan**: which test files cover this? Did you add new tests?
6. A maintainer will review within a few business days. Expect requests for
   changes; please respond or re-push within two weeks or the PR may be closed.
7. PRs are merged via squash-merge. The squash commit message should follow
   conventional commits style (`feat: ...`, `fix: ...`).

All contributors are expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

---

## 7. How to add a new detection signal

This is the highest-value contribution path for researchers who want to encode
domain knowledge about shadow fleet behaviour into the scoring engine.

The scoring pipeline lives in `backend/app/modules/risk_scoring.py` and is
driven by weights declared in `config/risk_scoring.yaml`. Scores compose as:

```
final_score = round(additive_subtotal × corridor_factor × vessel_size_factor)
```

The `compute_gap_score()` function is structured as numbered phases (Phase 6.1,
6.2, …). New signals should be appended as the next phase number.

### Step-by-step walkthrough

#### Step 1 — Declare the weight in `config/risk_scoring.yaml`

Add your signal's weight under an appropriate top-level key. Existing signals
use descriptive snake_case keys:

```yaml
# config/risk_scoring.yaml
gap_duration:
  ...

# Add a new top-level section:
my_new_signal:
  my_signal_pts: 25          # flat points awarded when condition is met
```

The config is loaded once at startup and cached. Running
`radiancefleet rescore-all-alerts` after deployment will apply the new weight
to all historical alerts.

#### Step 2 — Add the scoring phase to `risk_scoring.py`

Open `backend/app/modules/risk_scoring.py` and locate the `compute_gap_score()`
function. Add your phase after the last existing one, following the established
pattern:

```python
# Phase 6.13: My new signal — short human-readable description
my_cfg = config.get("my_new_signal", {})
my_signal_pts = int(my_cfg.get("my_signal_pts", 0))
if <your_condition_here>:
    breakdown["my_new_signal"] = my_signal_pts
```

For signals that require a database query (e.g. counting related events,
joining to another model), guard the phase with `if db is not None:` so that
unit tests that pass `db=None` continue to work:

```python
# Phase 6.13: Count port calls in the 30 days before the gap
if db is not None:
    port_cfg = config.get("my_new_signal", {})
    port_pts = int(port_cfg.get("my_signal_pts", 0))
    recent_port_calls = db.query(PortCall).filter(
        PortCall.vessel_id == gap.vessel_id,
        PortCall.arrival_utc >= gap.gap_start_utc - timedelta(days=30),
    ).count()
    if recent_port_calls >= 3:
        breakdown["my_new_signal"] = port_pts
```

The `breakdown` dict drives the explainability UI. Use descriptive key names;
keys prefixed with `_` are treated as metadata (multipliers, subtotals) and
are not summed by the front-end.

#### Step 3 — Write tests in `test_risk_scoring_complete.py`

Use the `_make_gap()` factory defined at the top of
`backend/tests/test_risk_scoring_complete.py`. It returns a fully-featured
`MagicMock`-based `AISGapEvent` that exercises the scoring engine without
requiring a live database:

```python
from tests.test_risk_scoring_complete import _make_gap
# — or inline in the test file —

def test_my_new_signal_fires():
    config = load_scoring_config()
    # Override the loaded config in-place for isolated testing:
    config.setdefault("my_new_signal", {})["my_signal_pts"] = 25

    gap = _make_gap(duration_minutes=600)   # 10-hour gap
    score, breakdown = compute_gap_score(gap, config)

    assert breakdown.get("my_new_signal") == 25


def test_my_new_signal_does_not_fire_when_below_threshold():
    config = load_scoring_config()
    config.setdefault("my_new_signal", {})["my_signal_pts"] = 25

    gap = _make_gap(duration_minutes=60)    # 1-hour gap — below trigger
    score, breakdown = compute_gap_score(gap, config)

    assert "my_new_signal" not in breakdown
```

Add both a positive case and at least one negative/boundary case.

#### Step 4 — Re-score historical alerts after deployment

After merging and deploying:

```bash
radiancefleet rescore-all-alerts
```

This clears all existing scores and recomputes them using the updated weights.
The command logs a short config hash so you can verify which version of
`risk_scoring.yaml` was applied.

---

## 8. How to add a new data source adapter

### 8a. OFAC-format sanctioned-vessel CSV

The `load_ofac_sdn()` function in
`backend/app/modules/watchlist_loader.py` reads the OFAC SDN CSV and matches
rows to `Vessel` records by MMSI, IMO, or fuzzy name (85 % threshold via
`rapidfuzz`). To add a new list that follows the same CSV shape:

1. Map the source CSV's column names to the internal field names expected by
   `_upsert_watchlist()`:

   ```python
   # backend/app/modules/watchlist_loader.py

   def load_my_new_list(db: Session, csv_path: str) -> dict:
       """Load MyNewList-format CSV into the watchlist."""
       field_map = {
           "Vessel_Name": "name",        # → matched against Vessel.vessel_name
           "MMSI_Number": "mmsi",        # → Vessel.mmsi
           "IMO_Number": "imo",          # → Vessel.imo_number
           "Listing_Date": "date_listed",
           "Reason_Code": "reason",
       }
       # ... read CSV, apply field_map, call _upsert_watchlist() per matched row
   ```

2. Expose a CLI command in `backend/app/cli.py` following the existing
   `watchlist import` command pattern:

   ```python
   @watchlist_app.command("import-mynewlist")
   def watchlist_import_mynewlist(path: Path = typer.Argument(...)):
       """Import MyNewList sanctioned vessels."""
       with get_db_session() as db:
           result = load_my_new_list(db, str(path))
       rich.print(result)
   ```

3. Write a unit test in `backend/tests/test_watchlist.py` that exercises the
   field mapping with a small in-memory CSV fixture.

### 8b. Custom AIS CSV

The `ingest.py` module accepts arbitrary CSV column layouts through a
field-renaming dict. To onboard a new AIS data provider:

1. Identify the provider's column names for the mandatory fields:
   `mmsi`, `timestamp_utc`, `lat`, `lon`, `sog` (speed over ground),
   `cog` (course over ground).

2. Pass a `rename_map` to the ingest pipeline:

   ```python
   # backend/app/modules/ingest.py

   rename_map = {
       "VesselMMSI":   "mmsi",
       "RecordTime":   "timestamp_utc",
       "Latitude":     "lat",
       "Longitude":    "lon",
       "SpeedKnots":   "sog",
       "CourseDeg":    "cog",
   }
   ingest_csv(db, path=csv_path, rename_map=rename_map)
   ```

3. Optional fields (`imo_number`, `vessel_name`, `ship_type`, `flag`,
   `deadweight`, `ais_class`) are ingested if present in the CSV after
   renaming; missing optional fields default to `None`.

4. `mmsi_first_seen_utc` is set on the `Vessel` record the first time a given
   MMSI is seen, enabling the new-MMSI scoring phase. This happens
   automatically in `ingest.py` and requires no adapter-specific code.

### 8c. Global Fishing Watch (GFW) format

GFW exports use a different schema (nested JSON, `apparent_fishing_hours`,
`vessel.ssvid` for MMSI). Follow the `backend/app/modules/gfw_import.py`
module pattern:

1. Copy `gfw_import.py` as a starting point for your new module.
2. Extract MMSI from `vessel.ssvid`, timestamp from `timestamp`, and position
   from `position.lon` / `position.lat`.
3. Map fishing activity flags to the `ais_class` or custom extended fields as
   appropriate for your use case.
4. Register the new module as a CLI subcommand under `radiancefleet ingest`.

---

## 9. Project layout reference

```
backend/
  app/
    models/          SQLAlchemy ORM models (14 models + 4 v1.1 stubs)
    modules/         Detection logic: ingest, gap_detector, risk_scoring,
                     loitering_detector, sts_detector, corridor_correlator,
                     satellite_query, evidence_export, watchlist_loader
    schemas/         Pydantic request/response schemas
    api/             FastAPI route handlers (20+ endpoints)
    cli.py           Typer CLI entry point (`radiancefleet` command)
    config.py        Settings loaded from environment / .env
  tests/             pytest test files
  pyproject.toml     Dependencies, ruff config, mypy config
config/
  corridors.yaml     11 seed corridors (export routes, STS zones, dark zones)
  risk_scoring.yaml  Scoring weights per PRD §7.5
frontend/
  src/               React 18 + TypeScript + TanStack Query + React-Leaflet
docker-compose.yml   PostgreSQL + PostGIS service definition
```

---

## Questions?

Open a GitHub Discussion or file an issue with the `question` label. For
security-sensitive findings (e.g. data exposure, auth bypass), please email
`conduct@radiancefleet.org` rather than filing a public issue.
