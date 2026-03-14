# API Reference

RadianceFleet exposes a REST API built with FastAPI. All endpoints are under the `/api/v1/` prefix.

---

## Auto-generated Documentation

When the server is running, interactive documentation is available at:

- `GET /docs` — Swagger UI (try-it-out, request builder)
- `GET /redoc` — ReDoc (readable reference format)

> **Note:** Docs are served at the root level (`/docs`, `/redoc`), not under `/api/v1/`.
> All API endpoints are under the `/api/v1/` prefix.

Start the server:

```bash
radiancefleet serve --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000/docs in a browser.

---

## Authentication

RadianceFleet supports dual authentication. Endpoints check both mechanisms and accept whichever is present:

1. **Public API key** (`X-API-Key` header): Admin-provisioned read-only keys created via `POST /admin/api-keys`. Suitable for external integrations, dashboards, and embeddable widgets. Each key has a `read_only` scope and a `30/minute` rate limit.
2. **JWT Bearer token** (`Authorization: Bearer <token>`): Obtained via `POST /admin/login`. Tokens carry `analyst_id`, `username`, and `role` claims. Roles: `analyst`, `senior_analyst`, `admin`. Required for all write operations (verdicts, assignments, locks, admin CRUD).

When `RADIANCEFLEET_API_KEY` is set in the environment, it acts as a global gate requiring a matching `X-API-Key` header on all requests (legacy mode).

Login:

```bash
curl -X POST "http://localhost:8000/api/v1/admin/login" \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "password": "secret"}'
# Returns: {"token": "eyJ...", "analyst": {...}}
```

Include the token on subsequent requests:

```bash
curl -H "Authorization: Bearer eyJ..." "http://localhost:8000/api/v1/alerts/my"
```

Or use a public API key for read-only access:

```bash
curl -H "X-API-Key: rf_abc123..." "http://localhost:8000/api/v1/alerts?min_score=76"
```

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
| 409 | Conflict (e.g. edit lock held by another analyst, version mismatch, corridor with linked events) |
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
| `GET` | `/vessels/{vessel_id}/track.geojson` | Export vessel AIS track as GeoJSON LineString (RFC 7946) |
| `GET` | `/vessels/{vessel_id}/track.kml` | Export vessel AIS track as KML with `gx:Track` timestamps |
| `GET` | `/vessels/{vessel_id}/psc-detentions` | PSC detention history for a vessel, ordered by detention date descending |

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
| `POST` | `/alerts/{alert_id}/export` | Export evidence card (json, md, csv, or **pdf**); blocked if status is `new` |
| `POST` | `/alerts/{alert_id}/export/gov-package` | Export government alert package combining evidence card and hunt context |
| `POST` | `/alerts/bulk-status` | Bulk-update status for multiple alerts in a single request |
| `POST` | `/alerts/{alert_id}/assign` | Assign an alert to an analyst (body: `{analyst_id}`) |
| `DELETE` | `/alerts/{alert_id}/assign` | Unassign an alert |
| `GET` | `/alerts/my` | List alerts assigned to the current analyst |
| `POST` | `/alerts/{alert_id}/lock` | Acquire an edit lock (returns 409 if held by another analyst) |
| `POST` | `/alerts/{alert_id}/lock/heartbeat` | Extend edit lock TTL |
| `DELETE` | `/alerts/{alert_id}/lock` | Release an edit lock |
| `POST` | `/alerts/{alert_id}/verdict` | Submit analyst verdict with optional version for optimistic locking |
| `POST` | `/evidence-cards/{card_id}/approve` | Approve an evidence card (senior_analyst or admin only) |
| `POST` | `/evidence-cards/{card_id}/reject` | Reject an evidence card with notes (senior_analyst or admin only) |

### Saved Filters

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/alerts/saved-filters` | List saved filters for the current analyst | JWT |
| `POST` | `/alerts/saved-filters` | Save a filter configuration (body: `{name, filter_json, is_default}`) | JWT |
| `DELETE` | `/alerts/saved-filters/{filter_id}` | Delete a saved filter (own filters only) | JWT |

### Alert Trends

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/alerts/trends` | Time-bucketed alert counts for trend charts; query param `period` (`7d`, `30d`, `90d`) | No |

### Satellite Orders

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/satellite/providers` | List configured satellite imagery providers and budget status |
| `GET` | `/satellite/orders` | List satellite orders (paginated, filterable by status/provider) |
| `GET` | `/satellite/orders/{order_id}` | Get satellite order detail |
| `POST` | `/satellite/orders/search` | Search provider archive for an alert's gap window |
| `POST` | `/satellite/orders/{order_id}/submit` | Submit a draft order (budget check enforced) |
| `POST` | `/satellite/orders/{order_id}/cancel` | Cancel a submitted order |
| `POST` | `/satellite/orders/poll` | Trigger status poll for active orders |
| `GET` | `/satellite/budget` | Current monthly spend and remaining budget |

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

### Merge Chains

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/merge-chains` | List merge chains with hydrated graph nodes and edges; filter by `min_confidence` or `confidence_band` |

### Coverage

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/coverage/geojson` | AIS coverage quality regions as GeoJSON FeatureCollection for map overlay |

### Admin

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/admin/login` | Analyst login (username + password); returns JWT access token |
| `POST` | `/admin/analysts` | Create a new analyst (admin only) |
| `GET` | `/admin/analysts` | List all analysts (admin only) |
| `PATCH` | `/admin/analysts/{analyst_id}` | Update analyst role, display name, or active status (admin only) |
| `POST` | `/admin/analysts/{analyst_id}/reset-password` | Reset an analyst's password (admin only) |
| `GET` | `/audit-log` | View the analyst action audit trail (see docs/METHODOLOGY.md for audit requirements); filterable by `action` and `entity_type`; paginated |

### API Keys

Manage public read-only API keys for external integrations. All endpoints require admin role.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `POST` | `/admin/api-keys` | Create a new read-only API key; returns the raw key once (body: `{name}`) | Admin |
| `GET` | `/admin/api-keys` | List all API keys (hashes excluded) | Admin |
| `DELETE` | `/admin/api-keys/{key_id}` | Deactivate an API key (soft delete) | Admin |

### Webhooks

Register webhook endpoints to receive HMAC-signed event notifications (3x retry on failure). All endpoints require admin role.

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `POST` | `/admin/webhooks` | Register a webhook endpoint (body: `{url, events, secret}`) | Admin |
| `GET` | `/admin/webhooks` | List all registered webhooks (secrets excluded) | Admin |
| `DELETE` | `/admin/webhooks/{webhook_id}` | Deactivate a webhook (soft delete) | Admin |
| `POST` | `/admin/webhooks/{webhook_id}/test` | Send a test event to a webhook URL | Admin |

### SSE (Server-Sent Events)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/sse/alerts` | Stream new alerts in real time via SSE; query params: `min_score` (default 51), `Last-Event-ID` for reconnection resume | JWT |

The SSE stream emits `alert` events with JSON payloads (`gap_event_id`, `vessel_id`, `risk_score`, `gap_start_utc`, `duration_minutes`, `status`) and periodic `ping` keepalives. Max 20 concurrent connections (configurable via `SSE_MAX_CONNECTIONS`). Returns 503 when the limit is reached.

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
| `assigned_to` | int | none | Filter by assigned analyst ID |
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
