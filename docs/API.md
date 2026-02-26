# API Reference

RadianceFleet exposes a REST API built with FastAPI. All endpoints are under the `/api/v1/` prefix.

---

## Auto-generated Documentation

When the server is running, interactive documentation is available at:

- `GET /api/v1/docs` — Swagger UI (try-it-out, request builder)
- `GET /api/v1/redoc` — ReDoc (readable reference format)

Start the server:

```bash
radiancefleet serve --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000/api/v1/docs in a browser.

---

## Authentication

Optional API key authentication. Set the `RADIANCEFLEET_API_KEY` environment variable to enable. When set, all requests must include the `X-API-Key` header. When unset, all requests pass without authentication (default for local dev).

---

## Error Format

All errors return JSON with a `detail` field and a standard HTTP status code:

```json
{"detail": "Alert not found"}
```

Common status codes:

| Code | Meaning |
|------|---------|
| 400 | Bad request (invalid body, blocked export) |
| 401 | Unauthorized (missing or invalid `X-API-Key` when auth is enabled) |
| 404 | Resource not found |
| 409 | Conflict (e.g. deleting a corridor that has linked gap events) |
| 422 | Validation error (FastAPI schema validation) |
| 429 | Rate limit exceeded (60 requests/minute per IP on read endpoints) |

---

## Rate Limiting

Read endpoints are rate-limited to 60 requests per minute per client IP (via slowapi). Exceeding the limit returns HTTP 429.

---

## Endpoint Reference

All paths below are relative to `/api/v1/`.

### Ingestion and Detection

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ais/import` | Upload AIS CSV file for ingestion; updates in-memory ingestion status |
| `GET` | `/ingestion-status` | Poll current AIS ingestion job status (idle / running / completed / failed) |
| `POST` | `/gaps/detect` | Run AIS gap detection over an optional date range |
| `POST` | `/spoofing/detect` | Run spoofing detection (impossible speed, anchor-in-ocean, circle spoof, etc.) |
| `GET` | `/spoofing/{vessel_id}` | List all spoofing anomalies for a vessel |
| `GET` | `/loitering/{vessel_id}` | List all loitering events for a vessel |
| `GET` | `/sts-events` | List recent ship-to-ship transfer events (last 100, descending) |
| `POST` | `/loitering/detect` | Run loitering detection and update laid-up vessel flags |
| `POST` | `/sts/detect` | Run STS transfer detection (run gap detection first) |
| `POST` | `/gfw/import` | Upload Global Fishing Watch vessel detection CSV |

### Vessels

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/vessels` | Search vessels by MMSI, IMO, or name; filter by flag or vessel type |
| `GET` | `/vessels/{vessel_id}` | Full vessel profile: watchlist, spoofing, loitering, STS, gap counts |
| `GET` | `/vessels/{vessel_id}/alerts` | All gap events for a vessel, sortable |
| `GET` | `/vessels/{vessel_id}/history` | Identity change history (renames, flag changes) |
| `GET` | `/vessels/{vessel_id}/watchlist` | Active watchlist entries for a vessel |

### Alerts

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/alerts` | List gap alerts with filtering by date, corridor, vessel, score, status; paginated |
| `GET` | `/alerts/map` | Lightweight map projection: returns up to 500 alerts with lat, lon, risk_score, vessel name, and gap duration for map markers |
| `GET` | `/alerts/export` | Bulk export alerts as a streaming CSV download |
| `GET` | `/alerts/{alert_id}` | Full alert detail including movement envelope, satellite check, AIS boundary points |
| `POST` | `/alerts/{alert_id}/status` | Update alert status (new / under_review / confirmed / dismissed) |
| `POST` | `/alerts/{alert_id}/notes` | Append analyst notes to an alert |
| `POST` | `/alerts/{alert_id}/satellite-check` | Prepare satellite check package for the alert's gap window |
| `POST` | `/alerts/{alert_id}/export` | Export evidence card for the alert (blocked if status is `new`) |
| `POST` | `/alerts/{alert_id}/export/gov-package` | Export government alert package combining evidence card and hunt context |
| `POST` | `/alerts/bulk-status` | Bulk-update status for multiple alerts in a single request |

### Corridors

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/corridors` | List all corridors with 7-day and 30-day alert counts |
| `POST` | `/corridors` | Create a new corridor (accepts optional WKT geometry) |
| `GET` | `/corridors/geojson` | Export all corridor geometries as a GeoJSON FeatureCollection for map overlay |
| `GET` | `/corridors/{corridor_id}` | Corridor detail with recent alert statistics |
| `PATCH` | `/corridors/{corridor_id}` | Update corridor metadata (geometry updates not allowed via API) |
| `DELETE` | `/corridors/{corridor_id}` | Delete a corridor (returns 409 if gap events are linked) |

### Watchlist

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/watchlist` | List all active watchlist entries, paginated |
| `POST` | `/watchlist` | Add a vessel to the local watchlist manually |
| `DELETE` | `/watchlist/{watchlist_entry_id}` | Soft-delete a watchlist entry (sets `is_active = false`) |
| `POST` | `/watchlist/import` | Batch-import watchlist from uploaded CSV/JSON (source: ofac, kse, opensanctions) |

### Scoring

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/score-alerts` | Score all unscored gap events using the risk scoring engine |
| `POST` | `/rescore-all-alerts` | Clear and re-compute all risk scores (use after `risk_scoring.yaml` changes) |

### Dark Vessels

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/dark-vessels` | List unmatched dark vessel detections; filterable by `ais_match_result` and `corridor_id`; paginated |
| `GET` | `/dark-vessels/{detection_id}` | Get full detail for a single dark vessel detection |

### Hunt

Vessel hunt endpoints implement FR9: given a gap event, compute a drift ellipse and score satellite-detected dark vessels as candidate re-appearances of the missing vessel.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/hunt/targets` | Register a vessel as a hunt target and create its target profile (DWT, speed class, last known position) |
| `GET` | `/hunt/targets` | List all vessel target profiles; paginated |
| `GET` | `/hunt/targets/{profile_id}` | Get a specific target profile |
| `POST` | `/hunt/missions` | Create a search mission with drift ellipse for a target profile and time window |
| `GET` | `/hunt/missions/{mission_id}` | Get search mission details including ellipse WKT and status |
| `POST` | `/hunt/missions/{mission_id}/find-candidates` | Score dark vessel detections within the mission drift ellipse and store as hunt candidates |
| `GET` | `/hunt/missions/{mission_id}/candidates` | List all hunt candidates for a mission |
| `POST` | `/hunt/missions/{mission_id}/confirm/{candidate_id}` | Confirm a candidate as the target vessel and mark the mission as finalized |

### Admin

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/audit-log` | View the analyst action audit trail (PRD NFR5); filterable by `action` and `entity_type`; paginated |

### System

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/stats` | Dashboard statistics: alert counts by severity, status, corridor; multi-gap vessels |
| `GET` | `/health` | Health check with database latency measurement |

---

## Query Parameter Reference

### `GET /alerts`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `date_from` | date (YYYY-MM-DD) | none | Filter gaps starting on or after this date |
| `date_to` | date (YYYY-MM-DD) | none | Filter gaps ending on or before this date |
| `corridor_id` | int | none | Filter by corridor |
| `vessel_id` | int | none | Filter by vessel |
| `min_score` | int | none | Minimum risk score (0–100) |
| `status` | string | none | Alert status: `new`, `under_review`, `confirmed`, `dismissed` |
| `sort_by` | string | `risk_score` | Sort field: `risk_score`, `gap_start_utc`, `duration_minutes` |
| `sort_order` | string | `desc` | `asc` or `desc` |
| `skip` | int | 0 | Pagination offset |
| `limit` | int | 50 | Pagination page size |

### `GET /vessels`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `search` | string | none | MMSI, IMO, or vessel name (partial name supported) |
| `flag` | string | none | Two-letter flag state code (e.g. `RU`, `PA`) |
| `vessel_type` | string | none | Vessel type substring match |
| `limit` | int | 20 | Maximum results |

---

## Example Requests

List high-priority unreviewed alerts:

```bash
curl "http://localhost:8000/api/v1/alerts?min_score=76&status=new&limit=10"
```

Export confirmed alerts as CSV:

```bash
curl "http://localhost:8000/api/v1/alerts/export?status=confirmed" \
  --output confirmed_alerts.csv
```

Update an alert to `under_review`:

```bash
curl -X POST "http://localhost:8000/api/v1/alerts/42/status" \
  -H "Content-Type: application/json" \
  -d '{"status": "under_review", "reason": "Checking satellite imagery for gap window"}'
```

Export an evidence card (JSON):

```bash
curl -X POST "http://localhost:8000/api/v1/alerts/42/export?format=json"
```

Upload AIS CSV:

```bash
curl -X POST "http://localhost:8000/api/v1/ais/import" \
  -F "file=@./data/aisdk_2024_01.csv"
```
