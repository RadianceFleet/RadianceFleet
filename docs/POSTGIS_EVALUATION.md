# PostGIS Migration Evaluation

## Summary

RadianceFleet currently uses Shapely for all spatial operations, storing geometry as WKT text in SQLite (or PostgreSQL text columns). This approach works correctly at the current scale of ~30 corridors and ~18 ports. A PostGIS migration is not justified today but should be revisited if corridor counts exceed 50 or spatial query latency becomes a bottleneck.

## Current State

### Models with geometry columns

| Model | Column | SQLAlchemy Type | Storage Format |
|-------|--------|----------------|----------------|
| `Corridor` | `geometry` | `Text` | WKT (POLYGON) |
| `DarkZone` | `geometry` | `Text` | WKT (POLYGON) |
| `MovementEnvelope` | `confidence_ellipse_geometry` | `Text` | WKT (POLYGON) |
| `Port` | `geometry` | `Text` | WKT (POINT) |
| `SatelliteCheck` | `query_geometry` | `String(2000)` | WKT (POLYGON) |

### Files importing Shapely

Shapely is used in 13 files across the codebase:

- `app/utils/geo.py` — core geometry utilities (WKT parse, distance, intersection)
- `app/modules/corridor_correlator.py` — polygon intersection for trajectory-corridor matching
- `app/modules/gap_detector.py` — movement envelope construction
- `app/cli_helpers.py` — corridor geometry loading from YAML
- `app/api/routes_detection.py` — spatial query construction for hunt/coverage endpoints
- `app/api/routes_alerts.py` — spatial filtering
- `app/modules/satellite_providers/planet_client.py` — AOI geometry for search queries
- `app/modules/satellite_providers/capella_client.py` — AOI geometry for search queries
- `app/modules/satellite_providers/maxar_client.py` — AOI geometry for search queries
- `app/modules/satellite_providers/umbra_client.py` — AOI geometry for search queries
- `scripts/seed_ports.py` — port geometry seeding
- `tests/test_stage3_a.py` — corridor geometry test fixtures
- `tests/test_port_resolver_fuzzy.py` — port geometry test fixtures

### How spatial operations work today

1. Geometry is stored as WKT text in the database.
2. On each spatial query, Python loads the WKT string and parses it into a Shapely object.
3. Intersection, containment, and distance checks run in Python memory via Shapely (GEOS library underneath).
4. Results are filtered in application code, not in SQL.

This means every corridor correlation query loads all corridor geometries from the database and tests each one in Python. At 30 corridors this is negligible (<10ms), but it scales linearly with corridor count.

## What PostGIS Provides

- **Native spatial SQL**: `ST_Intersects()`, `ST_Contains()`, `ST_DWithin()` run inside the database engine, eliminating the need to load all geometries into Python.
- **GiST indexes**: Spatial indexes enable sub-millisecond bounding-box pre-filtering, reducing full geometry checks to only overlapping candidates.
- **Spatial joins**: Queries like "find all gap events within any corridor" become single SQL statements instead of Python loops.
- **Geography type**: Native great-circle distance calculations without manual Haversine.
- **Standard compliance**: OGC Simple Features, well-supported by GeoAlchemy2.

## Migration Steps

1. **Add dependency**: `geoalchemy2>=0.15` to `pyproject.toml`.
2. **Schema migration**: Convert 5 geometry columns from `Text`/`String` to `Geometry` type with appropriate SRID (4326 for WGS84). Create GiST indexes on each.
3. **Data migration**: Parse existing WKT strings and insert as PostGIS geometry via `ST_GeomFromText()`.
4. **Refactor queries**: Replace Shapely intersection loops in `corridor_correlator.py`, `gap_detector.py`, `routes_detection.py`, and `routes_alerts.py` with `ST_Intersects()` SQL expressions.
5. **Satellite providers**: Update AOI geometry construction in all 4 provider clients to use GeoAlchemy2 or keep Shapely for outbound API formatting (no DB interaction).
6. **Update geo.py**: Retain Shapely utilities for non-DB geometry operations (envelope construction, GeoJSON serialization). Remove WKT round-trip code used for DB storage.
7. **Test updates**: Update fixtures in `test_stage3_a.py`, `test_port_resolver_fuzzy.py`, and any test that creates geometry via raw WKT strings.
8. **Drop SQLite support**: PostGIS requires PostgreSQL. The SQLite development path would need a Shapely fallback or be removed entirely.

## Effort Estimate

| Task | Estimate |
|------|----------|
| Schema + data migration script | 2-3 hours |
| GeoAlchemy2 model changes | 1-2 hours |
| Query refactor (corridor_correlator, gap_detector, routes) | 2-3 hours |
| Test updates | 1-2 hours |
| SQLite fallback decision + implementation | 1-2 hours |
| **Total** | **7-12 hours** |

## Trade-offs

### Benefits of PostGIS

- **Performance at scale**: O(log n) spatial index lookups vs O(n) Python loops. Matters when corridor count exceeds ~50 or when running spatial joins across thousands of gap events.
- **Query expressiveness**: Complex spatial queries (e.g., "all vessels within 10nm of any dark zone in the last 24h") become single SQL statements.
- **Reduced memory**: No need to load all geometries into Python for filtering.
- **Ecosystem**: Standard GIS tooling (QGIS, ogr2ogr) can connect directly to the database.

### Costs of PostGIS

- **SQLite compatibility loss**: RadianceFleet currently runs with zero external dependencies via SQLite. PostGIS requires a PostgreSQL server with the PostGIS extension, raising the barrier for local development and quick evaluation by journalists/researchers.
- **Docker-only local dev**: Without SQLite fallback, developers would need Docker or a local PostgreSQL+PostGIS installation.
- **Dependency weight**: GeoAlchemy2 adds another ORM abstraction layer.
- **Migration risk**: Data migration for existing deployments requires careful WKT-to-geometry conversion.

## Recommendation

**Defer the PostGIS migration.** The current Shapely approach is correct, well-tested, and performs adequately at the current scale. The primary value proposition of RadianceFleet is zero-dependency local setup via SQLite, which PostGIS would eliminate.

Revisit this decision when any of these conditions are met:

1. **Corridor count exceeds 50** — Python-side intersection loops will add noticeable latency.
2. **Spatial query latency exceeds 100ms** — measured via the slow query logging middleware added in v3.4.
3. **Complex spatial joins are needed** — e.g., cross-referencing gap events against dark zones and corridors simultaneously in SQL.
4. **SQLite is formally deprecated** — if all production deployments move to PostgreSQL, the SQLite compatibility concern becomes moot.

Until then, the Shapely approach provides the best trade-off between developer experience, deployment simplicity, and spatial correctness.
