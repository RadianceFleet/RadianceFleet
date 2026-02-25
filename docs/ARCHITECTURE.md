# RadianceFleet Architecture

## System Diagram

```
AIS CSV files
     │
     ▼
┌─────────────────┐
│   ingest.py     │  CSV parsing, field normalization, vessel upsert,
│                 │  VesselHistory change tracking, mmsi_first_seen_utc
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  normalize.py   │  Field coercion, MMSI dedup, flag risk classification
└────────┬────────┘
         │
         ▼
┌───────────────────────┐
│   gap_detector.py     │  CLASS_SPEEDS, Class B noise filter (<180s),
│                       │  haversine distance, velocity ratio,
│                       │  rotated ellipse envelopes, 5 spoofing typologies
└────────┬──────────────┘
         │ gap events
         ├────────────────────────────────────┐
         │                                    │
         ▼                                    ▼
┌────────────────────┐          ┌────────────────────────┐
│ loitering_detector │          │  corridor_correlator   │
│ .py                │          │  .py                   │
│                    │          │                        │
│ Polars 1h rolling  │          │ ST_Intersects on gap   │
│ SOG windows;       │          │ trajectory; assigns    │
│ laid-up 30d/60d    │          │ corridor_id,           │
│ flags; loiter-gap  │          │ dark_zone_id,          │
│ -loiter links      │          │ in_dark_zone flag      │
└────────┬───────────┘          └────────────┬───────────┘
         │                                   │
         │        ┌──────────────────────┐   │
         │        │   sts_detector.py    │   │
         │        │                      │   │
         │        │ Phase A: proximity   │   │
         │        │ 200m, 8+ windows;    │   │
         │        │ Phase B: approaching │   │
         │        │ vector + heading     │   │
         │        └──────────┬───────────┘   │
         │                   │               │
         └───────────────────┼───────────────┘
                             │ all signals
                             ▼
              ┌──────────────────────────┐
              │     risk_scoring.py      │
              │                          │
              │ 3-phase composition:     │
              │  1. Additive signals     │
              │  2. Corridor multiplier  │
              │  3. Vessel size mult.    │
              │                          │
              │ 12 signal categories,    │
              │ gap frequency subsumption│
              │ dark zone 3 scenarios    │
              └──────────────┬───────────┘
                             │ risk_score, risk_breakdown_json
                             ▼
              ┌──────────────────────────┐
              │   evidence_export.py     │
              │                          │
              │ Status gate (must not    │
              │ be "new"); Markdown +    │
              │ JSON export; regional    │
              │ AIS coverage metadata    │
              └──────────────┬───────────┘
                             │
                             ▼
                  Alert (EvidenceCard row)
```

---

## Module Breakdown

### ingest.py

Handles AIS CSV parsing and database insertion. Normalizes raw field names (including
variant MMSI/IMO spellings), coerces numeric types, and upserts `Vessel` records using
MMSI as the deduplication key. On each upsert, the module compares mutable fields
(flag, name, IMO, vessel type) against the stored values and writes a `VesselHistory`
row for any field that changed, forming a tamper-evident audit trail. It also tracks
`mmsi_first_seen_utc`: the timestamp at which the system first encountered a given MMSI
in the ingested data, which feeds the new-MMSI scoring signal in `risk_scoring.py`.

### gap_detector.py

The core gap detection engine. Iterates consecutive AIS points for each vessel and
fires a gap event when the time delta exceeds `GAP_MIN_HOURS` (configured in
`app/config.py`). A **Class B noise filter** skips intervals shorter than 180 seconds
to prevent AIS retransmission artifacts from producing spurious gaps. For each
qualifying gap the module uses the **haversine formula** to compute the great-circle
distance in nautical miles between the two boundary points, then derives the
**velocity plausibility ratio**: actual distance divided by the maximum possible
distance at the vessel's class-specific top speed (from the `CLASS_SPEEDS` lookup).
Ratios above 1.1 set `impossible_speed_flag=True`. A **rotated ellipse movement
envelope** is persisted for each gap using GeoAlchemy2 geometry, with semi-major and
semi-minor axes scaled from `max_speed * duration` and the heading of the pre-gap AIS
point; the estimation method (LINEAR / SPLINE / KALMAN) is selected by gap duration.
The module also runs **spoofing detection** covering five typologies: anchor spoof
(nav_status=1 for 72+ hours outside a known port or anchorage corridor), circle spoof
(tight positional cluster despite SOG > 3 kn in a 6-hour window), slow roll (SOG
0.5–2.0 kn sustained 12+ hours on a tanker), MMSI reuse (implied speed > 30 kn between
consecutive points), and nav status mismatch (nav_status=1 with SOG > 2 kn). An
additional erratic nav status detector fires on three sub-conditions: three or more
nav_status changes within 60 minutes, nav_status=3 sustained more than 6 hours on a
tanker, and nav_status=15 on a tanker.

### loitering_detector.py

Detects vessels exhibiting sustained low-speed movement that is a known precursor to
ship-to-ship transfers. Raw AIS points are loaded into a **Polars DataFrame** and
grouped into 1-hour buckets using `group_by_dynamic`. Per-bucket median SOG is computed,
and consecutive buckets with median SOG below 0.5 knots are collected into runs.
Runs lasting 4 or more hours produce a `LoiteringEvent` record. The risk score
component is 8 points for baseline loitering and 20 points when the event lasts 12 or
more hours and the position falls within a known corridor. The module also attempts
**loiter-gap-loiter link detection**: it scans for gap events ending within 48 hours
before the loiter start, and gap events beginning within 48 hours after the loiter end,
setting `preceding_gap_id` and `following_gap_id` FKs to surface the three-phase
dark-transfer pattern. A separate **laid-up vessel detector** uses daily Polars
`group_by_dynamic` aggregation: if a vessel's daily median position varies by less than
±0.033 degrees (approximately 2 nm) for 30 or 60 consecutive days, the corresponding
`vessel_laid_up_30d` or `vessel_laid_up_60d` flag is set on the `Vessel` row. If the
stable position overlaps an STS zone corridor bounding box, `vessel_laid_up_in_sts_zone`
is also set.

### sts_detector.py

Detects ship-to-ship oil transfer events using two sequential phases. **Phase A**
(confirmed visible-visible transfers) indexes all tanker AIS points into 15-minute
time buckets and then into a 1-degree lat/lon spatial grid, so only vessels sharing
a grid cell are compared, avoiding an O(n^2) full cross-product. Within each grid cell,
pairs passing within 200 metres of each other with SOG below 1 knot and parallel or
anti-parallel headings (within 30 degrees) accumulate passing windows. Eight or more
consecutive windows (two hours of sustained proximity) produce an `StsTransferEvent`.
An additional dark-vessel bonus of +15 points is applied if either vessel in the pair
had an overlapping AIS gap event. **Phase B** (approaching vectors) identifies
stationary tankers (SOG < 0.5 kn) whose latest AIS point falls inside an STS-zone
corridor, then finds other tankers approaching them (SOG 0.5–3 kn, bearing within 30
degrees of the stationary vessel). When estimated time of arrival is under 4 hours an
`approaching` StsTransferEvent is created. Deduplication prevents creating overlapping
records for the same vessel pair and time window.

### corridor_correlator.py

Links AIS gap events to maritime corridors and dark zones. For each gap, the module
constructs the straight-line trajectory between the gap's start and end AIS points and
tests it against every stored corridor polygon using **ST_Intersects**
(`ST_MakeLine` / `ST_MakePoint` via PostGIS or SpatiaLite). This trajectory-based
approach catches vessels that transit through a corridor without stopping inside it,
which a simpler `ST_Within` test on the gap endpoints would miss. When the spatial
extension is unavailable (SQLite without SpatiaLite loaded), the module falls back to a
bounding-box overlap check on the gap's endpoint coordinates, logging a one-time warning.
If multiple corridors intersect a gap trajectory, the one with the highest `risk_weight`
is selected. Dark zone correlation runs independently via `find_dark_zone_for_gap` and
sets `in_dark_zone=True` on the gap, allowing a gap to carry both a corridor association
and a dark zone association simultaneously.

### risk_scoring.py

The configurable risk scoring engine. Applies rules from `config/risk_scoring.yaml`
to produce an explainable integer score and a JSON breakdown dictionary for each
`AISGapEvent`. Scoring uses **three-phase composition**: Phase 1 sums additive signal
points across 12 categories; Phase 2 multiplies the subtotal by a corridor-type factor
(STS zones 2.0x, export routes 1.5x, legitimate trade routes 0.7x, standard 1.0x);
Phase 3 multiplies by a vessel-size factor (VLCC 1.5x, Suezmax 1.3x, Aframax 1.0x,
Panamax 0.8x). The 12 signal categories are: gap duration (5–55 pts by duration band),
speed anomaly with 1.4x gap duration bonus, movement envelope plausibility, dark zone
(three scenarios), gap-in-STS-corridor flat bonus, gap frequency with subsumption
hierarchy, flag state risk, vessel age, AIS class mismatch, spoofing signals (FK-deduped
to prevent double-counting), loitering events (including laid-up flags), STS transfer
events, watchlist hits (OFAC SDN/EU/KSE/local), vessel identity changes (flag, name,
MMSI changes), legitimacy signals (gap-free 90 days, consistent Class A), and new MMSI
scoring. The `scoring_date` parameter makes all time-relative calculations reproducible
(NFR3). Breakdown dict keys prefixed with `_` are metadata (multipliers, subtotals) and
are not summed by the UI.

### satellite_query.py

Interfaces with the Copernicus Data Space API to identify available satellite imagery
coverage for a gap's bounding box. Constructs a real geographic bounding box from the
gap's movement envelope and issues a search query against the Copernicus STAC API.
Computes a data source coverage quality label per region (GOOD / MODERATE / PARTIAL /
POOR / NONE) and stores scene references in `SatelliteCheck.scene_refs_json`. Coverage
metadata is surfaced in every evidence card per NFR7.

### evidence_export.py

Generates structured evidence cards in JSON and Markdown formats for analyst consumption.
The module enforces an **analyst review gate**: a gap event with status `"new"` cannot
be exported, ensuring that at least one analyst has reviewed the alert before it leaves
the system (NFR7). The exported card includes: vessel identity fields, gap start/end/
duration, full risk score breakdown, movement envelope metrics, last known AIS position
before the gap, first AIS position after the gap, satellite check status and scene
references, analyst notes, a regional AIS coverage quality table, and a mandatory
disclaimer that the output is investigative triage and not a legal determination.
Output is persisted as an `EvidenceCard` row. Markdown output uses human-readable
headers and a formatted score breakdown table.

---

## Why Sync SQLAlchemy

RadianceFleet uses synchronous SQLAlchemy rather than the async variant for two reasons.
First, both the CLI (Typer) and the API (FastAPI) need to share the same database session
and the same ORM models. The `get_db()` generator injects a `Session` into FastAPI's
`Depends()` machinery and is called directly from CLI commands, so a single sync session
type works for both callers without duplication. Second, the system is designed for a
single-analyst MVP workflow: there is no need for high concurrency or thousands of
simultaneous connections. The overhead of async plumbing (separate event loops, async
context managers, `await` chains through every layer) adds meaningful complexity without
a corresponding operational benefit at this scale.

---

## Why Polars in loitering_detector

The loitering detector requires **rolling time-series aggregation**: AIS points must be
grouped into 1-hour buckets and per-bucket median SOG must be computed before runs of
consecutive low-SOG buckets can be identified. Pandas does not provide a native temporal
`group_by_dynamic` operation equivalent; achieving the same result would require an
explicit `resample` + `apply` chain that is both slower and more verbose. Polars'
`group_by_dynamic` handles variable-interval time series efficiently in a single
expression, and its lazy evaluation plan avoids materializing intermediate frames. The
same `group_by_dynamic` call with `every="1d"` is reused in the laid-up vessel detector
for daily median position aggregation.

---

## Data Model ERD

```
Vessel
  vessel_id (PK)
  mmsi, imo, name, flag
  vessel_type, deadweight, year_built
  ais_class, flag_risk_category
  mmsi_first_seen_utc
  vessel_laid_up_30d, vessel_laid_up_60d
  vessel_laid_up_in_sts_zone
       │
       ├──────────────────────────────────────────────────────┐
       │                                                      │
       ▼                                                      ▼
  AISPoint                                            VesselHistory
    ais_point_id (PK)                                   history_id (PK)
    vessel_id (FK → Vessel)                             vessel_id (FK → Vessel)
    timestamp_utc, lat, lon                             field_changed
    sog, cog, heading                                   old_value, new_value
    nav_status, ais_class                               observed_at
       │
       │ start_point_id / end_point_id
       ▼
  AISGapEvent
    gap_event_id (PK)
    vessel_id (FK → Vessel)
    start_point_id (FK → AISPoint)
    end_point_id   (FK → AISPoint)
    corridor_id    (FK → Corridor)      ◄─────┐
    dark_zone_id   (FK → DarkZone)             │
    gap_start_utc, gap_end_utc                 │
    duration_minutes                           │
    risk_score, risk_breakdown_json            │
    status                                 Corridor
    impossible_speed_flag                    corridor_id (PK)
    velocity_plausibility_ratio              name, corridor_type
    in_dark_zone                             risk_weight
    pre_gap_sog                              is_jamming_zone
       │                                    geometry (POLYGON)
       │                                        │
       ├────────────────┐                       │ (M:N via corridor_id on AISGapEvent)
       │                │
       ▼                ▼
  SpoofingAnomaly   MovementEnvelope
    anomaly_id (PK)   envelope_id (PK)
    vessel_id         gap_event_id (FK → AISGapEvent)
    gap_event_id      envelope_semi_major_nm
    (FK → AISGapEvent,  envelope_semi_minor_nm
     nullable)        envelope_heading_degrees
    anomaly_type      estimated_method
    start_time_utc
    end_time_utc
    risk_score_component
    implied_speed_kn
       │
       ▼
  SatelliteCheck
    sat_check_id (PK)
    gap_event_id (FK → AISGapEvent)
    bounding_box_wkt
    review_status
    scene_refs_json
       │
       ▼
  EvidenceCard
    evidence_card_id (PK)
    gap_event_id (FK → AISGapEvent)
    version, export_format
    created_at

Vessel ──► LoiteringEvent
             loiter_id (PK)
             vessel_id (FK → Vessel)
             corridor_id (FK → Corridor, nullable)
             preceding_gap_id (FK → AISGapEvent, nullable)
             following_gap_id (FK → AISGapEvent, nullable)
             start_time_utc, end_time_utc
             duration_hours, median_sog_kn
             risk_score_component

Vessel ──► StsTransferEvent
             sts_id (PK)
             vessel_1_id (FK → Vessel)
             vessel_2_id (FK → Vessel)
             corridor_id (FK → Corridor, nullable)
             detection_type
             start_time_utc, end_time_utc
             mean_proximity_meters
             risk_score_component

Vessel ──► VesselWatchlist
             watchlist_id (PK)
             vessel_id (FK → Vessel)
             watchlist_source
             is_active
             matched_name, match_score

DarkZone
  zone_id (PK)
  name, geometry (POLYGON)
  └─ referenced by AISGapEvent.dark_zone_id
```

---

## API Architecture

All API routes are defined in a single `backend/app/api/routes.py` file using FastAPI's
`APIRouter()` pattern. The router is mounted on the main `app` with the prefix
`/api/v1/`. Every route that requires database access receives a session via
`Depends(get_db)`, where `get_db()` is a generator that opens a SQLAlchemy session and
closes it after the request completes. No route opens a session directly.

Route groupings (all under `/api/v1/`):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/vessels/search` | Vessel search with MMSI/name/flag filters |
| GET | `/vessels/{vessel_id}` | Vessel detail with recent alerts |
| GET | `/vessels/{vessel_id}/alerts` | All alerts for a vessel |
| GET | `/vessels/{vessel_id}/history` | VesselHistory change log |
| GET | `/vessels/{vessel_id}/watchlist` | Watchlist entries for a vessel |
| GET | `/alerts` | Alert list with status/score/corridor filters |
| GET | `/alerts/{alert_id}` | Alert detail with breakdown |
| PATCH | `/alerts/{alert_id}/status` | Update alert status |
| GET | `/alerts/{alert_id}/evidence` | Export evidence card (JSON or MD) |
| GET | `/alerts/export/csv` | Bulk CSV export (StreamingResponse) |
| GET | `/corridors` | List all corridors |
| GET | `/corridors/{corridor_id}` | Corridor detail |
| GET | `/watchlist` | List watchlist entries |
| POST | `/watchlist` | Add watchlist entry |
| DELETE | `/watchlist/{watchlist_id}` | Remove watchlist entry |
| POST | `/ingest/status` | Ingestion job status (in-memory app.state) |
| GET | `/stats` | Aggregate statistics |
| GET | `/health` | Health check |

---

## Configuration System

Two YAML files are loaded at application startup and passed into the scoring and
corridor systems.

**`config/corridors.yaml`** — defines 11 seed corridors: 4 export routes (Baltic,
Turkish Straits, Black Sea, Persian Gulf approaches), 5 STS zones (Laconian Gulf,
Ceuta, Kerch Strait anchorage, Singapore anchorage, Nakhodka Bay), and 2 dark zones
(Black Sea GPS-spoofing zone, Eastern Mediterranean GNSS-denial zone). Each corridor
entry specifies a name, type enum value, GeoJSON-compatible polygon coordinates, a
`risk_weight` (used to select the highest-priority corridor when multiple intersect a
gap trajectory), and an optional `is_jamming_zone: true` flag.

**`config/risk_scoring.yaml`** — defines all scoring weights by category: gap duration
band points, speed anomaly thresholds and points, movement envelope ratios, dark zone
scenario points, gap frequency window thresholds and points, flag state modifiers,
vessel age bands, AIS class mismatch points, STS signals, loitering signals, watchlist
source points, metadata change signals, legitimacy deductions, new MMSI signals, and
corridor and vessel size multipliers. The file is loaded once via
`load_scoring_config()` and cached in a module-level variable. `rescore_all_alerts()`
computes a SHA-256 hash of the config at rescore time for audit tracing.

Weights are passed into `compute_gap_score()` as the `config` dict argument so that
unit tests can supply a known configuration without touching the filesystem.

---

## Key Design Decisions

### ST_Intersects vs ST_Within

Corridor correlation uses `ST_Intersects(ST_MakeLine(start_point, end_point), corridor.geometry)`
rather than `ST_Within(endpoint, corridor.geometry)`. A vessel conducting a dark
transfer through the Laconian Gulf STS zone may switch off its AIS transmitter inside
the zone but have its last pre-gap AIS fix outside the zone boundary. A point-in-polygon
test on the endpoint would miss the event entirely; the trajectory intersection approach
catches the transit as long as the straight-line path from start to end crosses the
polygon. This significantly reduces false negatives for the shadow fleet use case where
AIS is disabled precisely at the point of interest.

### Gap Frequency Subsumption

When scoring gap frequency, only the highest-frequency window fires:

- 5 or more gaps in the last 30 days: +50 points
- 3 or more gaps in the last 14 days: +32 points (only if the 30d threshold was not met)
- 2 or more gaps in the last 7 days: +18 points (only if neither higher threshold was met)

Without subsumption, a vessel with 5 gaps in 30 days (which includes 3 in 14 days and
2 in 7 days) would score 100 points from frequency alone, distorting the final score.
Subsumption ensures the score reflects the worst observed pattern without stacking
overlapping windows.

### Dark Zone Three-Scenario Scoring

The dark zone signal has three distinct risk interpretations:

1. **Interior deduction (-10 pts)**: The gap is short (< 60 minutes) and the trajectory
   intersects a known GNSS-denial/jamming corridor. This is likely signal noise — the
   vessel briefly lost reception in a known interference zone. Points are subtracted to
   avoid false alarms in areas where AIS gaps are expected.

2. **Entry scenario (+20 pts)**: The gap is longer (>= 60 minutes) and the trajectory
   enters a dark zone (dark_zone_id is set, impossible_speed_flag is False). The vessel
   went dark for a meaningful duration in a known interference zone — suspicious but not
   impossible.

3. **Exit with impossible jump (+35 pts)**: The gap trajectory intersects a dark zone
   AND the reappearance position is geometrically impossible at the vessel's class speed
   (impossible_speed_flag is True). The vessel went dark, and the next known position
   cannot be explained by normal transit. This is the highest-confidence spoofing signal
   of the three.

### AIS Class B Threshold

The AIS class mismatch signal fires when a vessel uses a Class B transponder but has
deadweight above 1,000 tonnes. The threshold is 1,000 DWT (not 3,000), because SOLAS
Chapter V Regulation 19 requires Class A transponders on vessels above 300 gross
tonnage or making international voyages, and AIS Class B is typically for recreational
and small commercial vessels well below the 1,000 DWT mark. A large tanker using Class
B is either misconfigured or deliberately using lower-visibility equipment to reduce
traceability. The +50 point score reflects the high significance of this signal for
shadow fleet detection.
