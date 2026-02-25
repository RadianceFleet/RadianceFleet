# RadianceFleet Testing Guide

## Running Tests

```bash
cd backend && .venv/bin/pytest tests/ -v
```

## With Coverage

```bash
cd backend && .venv/bin/pytest tests/ --cov=app --cov-report=term-missing
```

To generate an HTML report:

```bash
cd backend && .venv/bin/pytest tests/ --cov=app --cov-report=html
```

The HTML report is written to `backend/htmlcov/index.html`.

---

## Test Structure

The test suite contains 115 tests across 9 focused test files. Each file is dedicated
to a single module or functional boundary.

### test_gap_detection.py

Covers the core gap detection algorithm in `gap_detector.py`:

- `_haversine_nm` accuracy against known coordinates
- `_class_speed` DWT bracket lookups (VLCC, Suezmax, Aframax, Panamax, default)
- Gap creation for a pair of AIS points exceeding `GAP_MIN_HOURS`
- Class B noise filter: intervals shorter than 180 seconds are skipped and no gap
  is created
- Velocity plausibility ratio calculation
- `impossible_speed_flag` set when ratio > 1.1
- Deduplication: re-running detection on the same vessel does not create duplicate gaps
- `detect_gaps_for_vessel` date range filtering

### test_spoofing.py

Covers all spoofing typology detectors in `gap_detector.run_spoofing_detection`:

- Anchor spoof: nav_status=1 for 72+ hours outside a port fires; inside a port does not
- Anchor spoof suppressed when position falls within an `anchorage_holding` corridor
- Circle spoof: tight cluster (std_dev < 0.05 deg) with median SOG > 3 kn fires
- Circle spoof not fired near a port
- Slow roll: 0.5–2.0 kn sustained 12+ hours on a tanker fires; non-tanker does not
- MMSI reuse: implied speed > 30 kn between consecutive points fires; < 30 does not
- Nav status mismatch: nav_status=1 with SOG > 2 kn fires
- Erratic nav status: 3+ changes in 60 minutes fires; 2 changes does not fire
- Erratic nav status: continuous multi-window episode produces exactly one anomaly
  (non-overlapping window advance logic)
- Extended restricted maneuverability (nav_status=3 > 6h on tanker): fires
- nav_status=15 on tanker: fires with score 5

### test_loitering.py

Covers `loitering_detector.py` including both detection modes:

- AIS points loaded into Polars DataFrame and bucketed into 1-hour windows
- Median SOG below threshold for 4+ hours produces a `LoiteringEvent`
- Runs shorter than 4 hours do not fire
- Runs of 12+ hours in a corridor produce risk_score_component=20; baseline is 8
- Loiter-gap-loiter link: `preceding_gap_id` and `following_gap_id` set correctly
- Deduplication: re-running on same vessel does not create duplicate records
- Laid-up 30d flag set when median daily position stable for 30+ consecutive days
- Laid-up 60d flag set at 60 days
- `vessel_laid_up_in_sts_zone` flag set when stable position overlaps an STS zone bbox

### test_corridor.py

Covers `corridor_correlator.py`:

- `find_corridor_for_gap` using ST_Intersects trajectory intersection
- Bounding-box fallback path when spatial extension is unavailable
- Gap trajectory passing through a corridor (not ending inside it) is correctly matched
- `find_dark_zone_for_gap` returns the lowest zone_id when multiple zones match
- `correlate_all_uncorrelated_gaps` batch function: sets corridor_id and in_dark_zone
- is_jamming_zone=True on matched corridor also sets in_dark_zone=True

### test_risk_scoring_complete.py

The largest test file: 47 tests covering all 12 signal categories in `risk_scoring.py`.
All tests are unit-level; no database is required. Each test uses the `_make_gap()`
factory (described below) to construct a `MagicMock` gap event.

Signal categories tested:

- Gap duration bands (2–4h, 4–8h, 8–12h, 12–24h, 24h+)
- Speed spike and speed spoof before gap; 1.4x duration bonus
- Impossible reappear (+40) and near-impossible reappear (+15)
- Dark zone interior deduction (-10), entry (+20), exit with impossible jump (+35)
- Gap in STS corridor flat bonus (+30)
- Gap frequency subsumption: 30d (50pts) supersedes 14d (32pts) supersedes 7d (18pts)
- Flag state: white list (-10), high risk (+15)
- Vessel age bands and age+high_risk_flag combination (+30)
- AIS class mismatch for DWT > 1000 (+50); not fired for DWT <= 1000
- Legitimacy signals skipped when db=None
- New MMSI scoring (< 30 days old: +15)
- New MMSI + Russian-origin flag stacking (+25 additional)
- Score reproducibility: same gap with same `scoring_date` produces identical output
- `_score_band` thresholds: low (0–20), medium (21–50), high (51–75), critical (76+)
- Corridor multiplier applied correctly for sts_zone, export_route, legitimate_trade_route
- Vessel size multiplier applied correctly for VLCC, Suezmax, Aframax, Panamax
- Metadata keys (`_corridor_type`, `_vessel_size_class`, etc.) present in breakdown
- Laid-up vessel scoring: 30d (+15), 60d (+25), in_sts_zone (+30)

### test_evidence_export.py

Covers `evidence_export.py`:

- Export returns error when alert status is `"new"` (NFR7 gate)
- Export succeeds when status is `"reviewed"` or `"confirmed"`
- JSON format output contains all mandatory fields from PRD §7.7
- Markdown format output contains vessel, gap, risk score, movement envelope, and
  AIS boundary point sections
- `DISCLAIMER` string is included in every export
- Regional AIS coverage quality label is included in the card
- EvidenceCard row is persisted to the database on successful export
- Alert not found returns `{"error": "Alert not found"}`
- Unsupported format returns `{"error": ...}`

### test_gfw_import.py

Covers the Global Fishing Watch CSV import command:

- GFW CSV rows are parsed and mapped to AISPoint records
- Vessels not yet in the database are created during import
- Duplicate MMSI rows are deduplicated correctly
- Timestamp parsing handles the GFW UTC format

### test_api_alerts.py

Integration tests for the alert API endpoints using the `api_client` fixture:

- `GET /api/v1/alerts` returns a list with status and score filters applied
- `GET /api/v1/alerts/{alert_id}` returns full alert detail including breakdown
- `PATCH /api/v1/alerts/{alert_id}/status` updates status and returns 200
- `GET /api/v1/alerts/{alert_id}/evidence` returns evidence card JSON
- `GET /api/v1/alerts/export/csv` returns streaming CSV with correct headers

### test_api_vessels.py

Integration tests for the vessel API endpoints using the `api_client` fixture:

- `GET /api/v1/vessels/search` with MMSI, name, and flag query parameters
- `GET /api/v1/vessels/{vessel_id}` returns vessel detail with linked alert count
- `GET /api/v1/vessels/{vessel_id}/history` returns VesselHistory change log
- `GET /api/v1/vessels/{vessel_id}/watchlist` returns active watchlist entries
- 404 returned for unknown vessel_id

---

## Fixture Pattern

### mock_db

Defined in `backend/tests/conftest.py`. Creates a `MagicMock` SQLAlchemy session with
pre-configured return values for the most common ORM call chains:

```python
@pytest.fixture
def mock_db():
    """MagicMock database session — returns None for all queries by default."""
    session = MagicMock()
    # Default: query().filter().first() returns None (not found)
    session.query.return_value.filter.return_value.first.return_value = None
    session.query.return_value.filter.return_value.filter.return_value.all.return_value = []
    session.query.return_value.filter.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    return session
```

By default every query returns `None` or `[]`. Individual tests override specific
call chains by assigning new `return_value` or `side_effect` values to the mock:

```python
def test_alert_found(mock_db):
    gap = MagicMock()
    gap.gap_event_id = 42
    gap.status = "reviewed"
    mock_db.query.return_value.filter.return_value.first.return_value = gap
    ...
```

Because `MagicMock` returns another `MagicMock` for any unspecified attribute access,
all numeric fields used in scoring (deadweight, velocity_plausibility_ratio, etc.) must
be explicitly set to real `int` or `float` values. The `compute_gap_score` function
includes `isinstance(val, (int, float))` guards at every point where a MagicMock return
value would otherwise cause arithmetic errors.

### api_client

Also defined in `conftest.py`. Uses FastAPI's `dependency_overrides` mechanism to
replace `get_db` with a generator that yields the `mock_db` fixture:

```python
@pytest.fixture
def api_client(mock_db):
    """TestClient with DB dependency overridden to use a MagicMock session."""
    def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
```

Tests that use `api_client` make HTTP requests through the FastAPI application stack
without a real database. The `mock_db` fixture is still accessible in the test body for
configuring return values before each request.

---

## _make_gap() Helper

The `_make_gap()` factory in `test_risk_scoring_complete.py` constructs a fully-featured
mock `AISGapEvent` suitable for passing directly to `compute_gap_score()`. It exists
because the scoring function accesses many fields through `gap.vessel.*` and
`gap.corridor.*` relationships, and building these by hand in every test would be
verbose and fragile.

### Signature

```python
def _make_gap(
    duration_minutes=0,
    corridor_type=None,       # plain string: "sts_zone", "export_route", etc.
    deadweight=None,          # float DWT; None → sub-Panamax default multiplier
    flag_risk="unknown",      # "low_risk", "high_risk", or "unknown"
    year_built=None,          # int; None → vessel age signal skipped
    ais_class="unknown",      # "A" or "B"; triggers class mismatch check
    impossible_speed_flag=False,
    velocity_ratio=None,      # float; used for near_impossible_reappear signal
    in_dark_zone=False,
    dark_zone_id=None,        # int → dark zone with explicit ID; None → jamming corridor path
    mmsi_first_seen_utc=None, # datetime; triggers new_mmsi scoring if < 30d old
    flag=None,                # ISO flag code string; checked against RUSSIAN_ORIGIN_FLAGS set
    vessel_laid_up_30d=False,
    vessel_laid_up_60d=False,
    vessel_laid_up_in_sts_zone=False,
) -> MagicMock:
```

### Key Implementation Details

- `gap.vessel` is a `MagicMock` with all vessel fields set explicitly to real Python
  types (not MagicMock instances). This is essential because `compute_gap_score` uses
  `isinstance(val, (int, float))` guards to detect MagicMock objects.
- `gap.corridor` is `None` when `corridor_type=None`; otherwise a `MagicMock` with
  `corridor.corridor_type` set to the plain string (e.g. `"sts_zone"`). Plain strings
  work with the `hasattr(obj, "value")` dispatch in `_corridor_multiplier`.
- `gap.gap_start_utc` and `gap.gap_end_utc` are fixed to known datetimes so that
  frequency window calculations are deterministic.
- `gap.vessel_id` and `gap.gap_event_id` are both set to `1`.

### Example Usage

```python
def test_sts_corridor_flat_bonus():
    config = load_scoring_config()
    gap = _make_gap(duration_minutes=8 * 60, corridor_type="sts_zone")
    score, breakdown = compute_gap_score(gap, config)
    assert "gap_in_sts_tagged_corridor" in breakdown
    assert breakdown["gap_in_sts_tagged_corridor"] == 30
```

---

## How to Add a New Scoring Signal Test

Follow these steps when adding a new signal to `compute_gap_score` and writing its test.

**Step 1.** Add the signal to `config/risk_scoring.yaml` under the appropriate category
key. Example:

```yaml
new_category:
  my_new_signal: 25
```

**Step 2.** Add the scoring logic to `compute_gap_score` in `risk_scoring.py`. The
logic should read from `config.get("new_category", {}).get("my_new_signal", 25)` and
write to `breakdown["my_new_signal"]`.

**Step 3.** Add a passing test. Use `_make_gap()` to construct a gap with the fields
needed to trigger the signal, then assert that the breakdown key is present and has the
expected value:

```python
def test_my_new_signal_fires():
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=4 * 60,
        # ... set fields that trigger the new signal
    )
    score, breakdown = compute_gap_score(gap, config)
    assert "my_new_signal" in breakdown
    assert breakdown["my_new_signal"] == 25
```

**Step 4.** Add an edge case test that confirms the signal does NOT fire when the
condition is not met:

```python
def test_my_new_signal_does_not_fire_below_threshold():
    config = load_scoring_config()
    gap = _make_gap(
        duration_minutes=4 * 60,
        # ... fields that do NOT meet the threshold
    )
    score, breakdown = compute_gap_score(gap, config)
    assert "my_new_signal" not in breakdown
```

**Step 5.** If the signal depends on database records (watchlist, spoofing anomalies,
loitering events), use `mock_db` from `conftest.py` and configure the appropriate
`query().filter().all()` return value before calling `compute_gap_score(..., db=mock_db)`.

**Step 6.** Verify the full suite still passes: `cd backend && .venv/bin/pytest tests/ -v`.

---

## Sample Data

`scripts/generate_sample_data.py` inserts seven vessels with realistic AIS trajectories
into the database for end-to-end testing and demonstration. Each vessel exercises a
different detection scenario:

| Vessel | MMSI | Scenario | What fires |
|--------|------|----------|------------|
| A | 123456789 | 26-hour AIS gap | gap_detector + risk_scoring; gap_duration_24h_plus (+55) |
| B | 234567890 | Circle spoof | run_spoofing_detection CIRCLE_SPOOF; tight lat/lon cluster with SOG > 3 kn |
| C | 345678901 / 456789012 | STS pair | sts_detector Phase A; two tankers within 200m for 2+ hours |
| D | 567890123 | Watchlist hit | vessel inserted into VesselWatchlist with source OFAC_SDN |
| E | 678901234 | New MMSI | mmsi_first_seen_utc set to < 30 days before gap; new_mmsi_first_30d fires |
| F | 789012345 | Clean control | No anomalies; used to verify zero-score baseline |
| G | 890123456 | Impossible reappear | Consecutive AIS points with implied speed > 30 kn; impossible_speed_flag=True |

To populate a local database with sample data:

```bash
source backend/.venv/bin/activate
python scripts/generate_sample_data.py
```

---

## Coverage Targets

The suite currently contains **115 tests** (as of v1.0). The following targets apply
when contributing new signals, detectors, or API endpoints:

- Every new scoring signal requires at minimum:
  - 1 passing test confirming the signal fires and the breakdown key has the correct value
  - 1 edge case test confirming the signal does not fire below the threshold
- Every new detector (loitering-style or spoofing-style) requires:
  - 1 test per detection typology
  - 1 deduplication test (re-running does not create duplicate records)
- Every new API endpoint requires:
  - 1 happy-path test using `api_client`
  - 1 not-found / error test

Coverage for `app/modules/` should remain above 80%. Run
`pytest --cov=app --cov-report=term-missing` to identify uncovered lines before
submitting a pull request.
