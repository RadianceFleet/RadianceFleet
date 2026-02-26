# API Integration Guide

Practical guide for connecting external tools, scripts, and notebooks to the RadianceFleet API. For the full endpoint reference see [API.md](API.md).

Base URL throughout this guide: `http://localhost:8000/api/v1`

---

## Before You Start

**Start the server:**

```bash
radiancefleet serve --host 127.0.0.1 --port 8000
```

**Authentication:**

The API supports optional key-based authentication via the `X-API-Key` header. Set the environment variable before starting the server:

```bash
export RADIANCEFLEET_API_KEY="your-secret-key"
radiancefleet serve
```

If `RADIANCEFLEET_API_KEY` is not set, the API runs without authentication (single-analyst local use). When auth is enabled, include the header on every request:

```
X-API-Key: your-secret-key
```

**Interactive docs (no client required):**

- Swagger UI (try-it-out): http://localhost:8000/docs
- ReDoc (readable reference): http://localhost:8000/redoc
- OpenAPI spec (JSON): http://localhost:8000/openapi.json

---

## curl Examples

### List high-risk alerts

```bash
curl -s "http://localhost:8000/api/v1/alerts?min_score=76&status=new&limit=10" \
  -H "X-API-Key: ${RADIANCEFLEET_API_KEY}" | jq .
```

Response shape:

```json
{
  "items": [
    {
      "gap_event_id": 42,
      "vessel_name": "OCEAN PIONEER",
      "vessel_mmsi": "123456789",
      "risk_score": 88,
      "status": "new",
      "gap_start_utc": "2026-01-14T03:12:00",
      "duration_minutes": 1823,
      "last_lat": 55.21,
      "last_lon": 19.87
    }
  ],
  "total": 7
}
```

### Get vessel detail

```bash
curl -s "http://localhost:8000/api/v1/vessels/42" \
  -H "X-API-Key: ${RADIANCEFLEET_API_KEY}" | jq .
```

### Update alert status

```bash
curl -s -X POST "http://localhost:8000/api/v1/alerts/42/status" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${RADIANCEFLEET_API_KEY}" \
  -d '{"status": "under_review", "reason": "Corroborated by satellite pass 2026-01-14"}'
```

Response:

```json
{"status": "ok", "new_status": "under_review"}
```

### Export alerts as CSV

```bash
curl -s "http://localhost:8000/api/v1/alerts/export?status=confirmed" \
  -H "X-API-Key: ${RADIANCEFLEET_API_KEY}" \
  -o alerts_confirmed.csv
```

Supports additional filters: `date_from`, `date_to`, `min_score`. The response streams directly — no memory buffering on the server.

### Prepare satellite check

```bash
curl -s -X POST "http://localhost:8000/api/v1/alerts/42/satellite-check" \
  -H "X-API-Key: ${RADIANCEFLEET_API_KEY}" | jq .
```

Returns a Copernicus bounding box and suggested acquisition window for the gap period.

### Health check

```bash
curl -s "http://localhost:8000/api/v1/health"
```

```json
{"status": "ok", "db": "connected"}
```

---

## Python (httpx)

`httpx` is already a project dependency. The script below covers the main integration patterns.

```python
import httpx
import os
import csv
import io
from datetime import date, timedelta

BASE_URL = "http://localhost:8000/api/v1"
API_KEY  = os.getenv("RADIANCEFLEET_API_KEY", "")

headers = {"X-API-Key": API_KEY} if API_KEY else {}

client = httpx.Client(base_url=BASE_URL, headers=headers, timeout=30)


# --- Search vessels ---
def search_vessels(query: str) -> list[dict]:
    r = client.get("/vessels", params={"search": query, "limit": 20})
    r.raise_for_status()
    return r.json()["items"]

results = search_vessels("PIONEER")
for v in results:
    print(v["name"], v["mmsi"], "score:", v["last_risk_score"])


# --- Paginate all high-risk alerts ---
def iter_alerts(min_score: int = 76, page_size: int = 50):
    skip = 0
    while True:
        r = client.get("/alerts", params={
            "min_score": min_score,
            "skip": skip,
            "limit": page_size,
            "sort_by": "risk_score",
            "sort_order": "desc",
        })
        r.raise_for_status()
        data = r.json()
        yield from data["items"]
        skip += page_size
        if skip >= data["total"]:
            break

for alert in iter_alerts(min_score=76):
    print(alert["gap_event_id"], alert["vessel_name"], alert["risk_score"])


# --- Export evidence card for a reviewed alert ---
def export_evidence(alert_id: int, fmt: str = "json") -> dict:
    r = client.post(f"/alerts/{alert_id}/export", params={"format": fmt})
    r.raise_for_status()
    return r.json()

card = export_evidence(42)
# Note: export is blocked when status == "new" (NFR7 analyst gate)
# Update status first: POST /alerts/{id}/status {"status": "under_review"}


# --- Bulk CSV download ---
def download_csv(path: str, **filters) -> None:
    with client.stream("GET", "/alerts/export", params=filters) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=65536):
                f.write(chunk)

download_csv("high_risk.csv", min_score=76, status="documented")

client.close()
```

---

## Python (Jupyter / pandas)

```python
import httpx
import pandas as pd
import os

BASE_URL = "http://localhost:8000/api/v1"
headers  = {"X-API-Key": k} if (k := os.getenv("RADIANCEFLEET_API_KEY")) else {}

# Fetch up to 500 alerts into a DataFrame
r = httpx.get(f"{BASE_URL}/alerts", headers=headers,
               params={"limit": 500, "sort_by": "risk_score", "sort_order": "desc"})
r.raise_for_status()

df = pd.DataFrame(r.json()["items"])
df["gap_start_utc"] = pd.to_datetime(df["gap_start_utc"])
df["duration_hours"] = df["duration_minutes"] / 60

# Score distribution
print(df["risk_score"].describe())
print("\nStatus breakdown:")
print(df["status"].value_counts())

# High-risk by flag
high = df[df["risk_score"] >= 76].copy()
print("\nTop flags in high-risk alerts:")
print(high.groupby("vessel_mmsi")["risk_score"].max().sort_values(ascending=False).head(10))

# Plot (requires matplotlib)
df["risk_score"].plot.hist(bins=20, title="Risk Score Distribution")
```

---

## JavaScript (fetch)

Works in Node.js (18+) and modern browsers.

```javascript
const BASE = "http://localhost:8000/api/v1";
const KEY  = process.env.RADIANCEFLEET_API_KEY ?? "";
const hdrs = KEY ? { "X-API-Key": KEY } : {};

// List high-risk alerts
async function listAlerts({ minScore = 76, status = "new", limit = 50 } = {}) {
  const url = new URL(`${BASE}/alerts`);
  url.searchParams.set("min_score", minScore);
  url.searchParams.set("status", status);
  url.searchParams.set("limit", limit);
  const res = await fetch(url, { headers: hdrs });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

// Get vessel detail
async function getVessel(vesselId) {
  const res = await fetch(`${BASE}/vessels/${vesselId}`, { headers: hdrs });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

// Update alert status
async function updateStatus(alertId, status, reason = "") {
  const res = await fetch(`${BASE}/alerts/${alertId}/status`, {
    method: "POST",
    headers: { ...hdrs, "Content-Type": "application/json" },
    body: JSON.stringify({ status, reason }),
  });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

// Stream CSV export to file (Node.js)
import { createWriteStream } from "fs";
import { pipeline } from "stream/promises";
import { Readable } from "stream";

async function exportCSV(dest, params = {}) {
  const url = new URL(`${BASE}/alerts/export`);
  Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const res = await fetch(url, { headers: hdrs });
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  await pipeline(Readable.fromWeb(res.body), createWriteStream(dest));
}

// Health check
async function health() {
  const res = await fetch(`${BASE}/health`);
  return res.json();
}

// Example usage
const { items, total } = await listAlerts({ minScore: 76 });
console.log(`${total} high-risk alerts, showing ${items.length}`);
await updateStatus(items[0].gap_event_id, "under_review", "Queued for satellite check");
await exportCSV("./export.csv", { status: "documented", min_score: 76 });
```

---

## Common Workflows

### Daily monitoring script

Pull new high-risk alerts since yesterday and print a triage summary:

```bash
#!/usr/bin/env bash
set -euo pipefail

YESTERDAY=$(date -u -d "yesterday" +%Y-%m-%d)
TODAY=$(date -u +%Y-%m-%d)
BASE="http://localhost:8000/api/v1"

echo "=== RadianceFleet daily briefing: ${TODAY} ==="

curl -s "${BASE}/alerts?min_score=76&status=new&date_from=${YESTERDAY}&date_to=${TODAY}&limit=50" \
  -H "X-API-Key: ${RADIANCEFLEET_API_KEY}" \
  | jq -r '.items[] | "\(.risk_score)\t\(.vessel_name)\t\(.vessel_mmsi)\t\(.gap_start_utc)"' \
  | sort -rn

echo ""
echo "Total new high-risk: $(curl -s "${BASE}/alerts?min_score=76&status=new&limit=1" \
  -H "X-API-Key: ${RADIANCEFLEET_API_KEY}" | jq .total)"
```

### Batch CSV export

Export all documented alerts for a date range into a timestamped file:

```bash
#!/usr/bin/env bash
OUT="radiancefleet_$(date -u +%Y%m%d).csv"
curl -s "http://localhost:8000/api/v1/alerts/export?status=documented&min_score=50" \
  -H "X-API-Key: ${RADIANCEFLEET_API_KEY}" \
  -o "${OUT}"
echo "Saved ${OUT} ($(wc -l < "${OUT}") rows)"
```

### OSINT toolchain integration

Feed vessel MMSIs to an external lookup tool (e.g. MarineTraffic, VesselFinder):

```python
import httpx
import subprocess
import os

BASE    = "http://localhost:8000/api/v1"
headers = {"X-API-Key": os.getenv("RADIANCEFLEET_API_KEY", "")}

r = httpx.get(f"{BASE}/alerts", headers=headers,
               params={"min_score": 76, "status": "new", "limit": 50})
r.raise_for_status()
mmsis = {a["vessel_mmsi"] for a in r.json()["items"] if a["vessel_mmsi"]}

for mmsi in sorted(mmsis):
    # Pipe MMSI list to your own OSINT enrichment script
    subprocess.run(["./enrich_vessel.sh", mmsi], check=False)
```

---

## Validation Rules and Common Mistakes

| Field | Rule | Common mistake |
|---|---|---|
| `mmsi` | Exactly 9 digits, passed as **string** | Passing as integer (leading zeros lost) |
| `date_from` / `date_to` | ISO 8601 `YYYY-MM-DD`; `date_from <= date_to` | Reversed range → 422 |
| `status` | One of: `new`, `under_review`, `needs_satellite_check`, `documented`, `dismissed` | Typos, wrong case |
| `risk_score` | Integer 0–100 | Scores outside range will not match any alert |
| Evidence export | Blocked when `status == "new"` (NFR7) | Call `POST /alerts/{id}/status` first to advance status |
| Gap detection order | Run `detect-gaps` before `detect-sts` | STS detector requires gap events to exist |
| `skip` / `limit` | `limit` max is **500** (MAX_QUERY_LIMIT) | Requesting `limit=10000` silently clamps to 500 |

---

## Pagination

All list endpoints use `skip` / `limit` query parameters.

| Endpoint | Default `limit` | Notes |
|---|---|---|
| `/alerts` | 50 | Hard cap: 500 |
| `/vessels` | 20 | Hard cap: 500 |
| `/sts-events` | 50 | Hard cap: 500 |

Iterating all pages:

```python
def paginate(client, path, **params):
    skip, page_size = 0, params.pop("limit", 50)
    while True:
        r = client.get(path, params={**params, "skip": skip, "limit": page_size})
        r.raise_for_status()
        data = r.json()
        yield from data["items"]
        skip += page_size
        if skip >= data["total"]:
            break
```

---

## Error Handling

All errors return JSON:

```json
{"detail": "Alert not found"}
```

| Status | Meaning | Common cause |
|---|---|---|
| 401 | API key invalid | Wrong or missing `X-API-Key` when auth is enabled |
| 404 | Not found | Wrong ID in URL path |
| 409 | Conflict | Deleting a corridor with linked gap events |
| 422 | Validation error | Invalid param type, reversed date range, unknown status value |
| 429 | Rate limit | More than 60 read requests per minute |

Retry pattern for 429:

```python
import time

def get_with_retry(client, url, **params):
    for attempt in range(3):
        r = client.get(url, params=params)
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Rate limit exceeded after retries")
```

---

## Complex Response Fields Explained

### `risk_breakdown_json`

Present on individual alert detail (`GET /alerts/{id}`). Contains the per-signal scoring breakdown used to produce the final `risk_score`.

```json
{
  "gap_frequency_30d": 50,
  "dark_zone_entry": 20,
  "impossible_speed": 35,
  "sts_event": 18,
  "loitering": 15,
  "watchlist_hit": 25,
  "corridor_multiplier": 1.3,
  "vessel_size_multiplier": 1.1,
  "legitimacy_deductions": -20,
  "final_score": 88
}
```

Signal contributions are additive before multipliers. Legitimacy deductions (P&I coverage, port calls, etc.) are subtracted at face value and are not scaled by multipliers.

### `movement_envelope`

A rotated confidence ellipse (GeoJSON `Polygon`) representing the plausible sea area a vessel could have reached during the gap, computed from the gap duration and vessel class speed.

```json
{
  "envelope_id": 7,
  "max_plausible_distance_nm": 312.5,
  "actual_gap_distance_nm": 87.3,
  "velocity_plausibility_ratio": 0.28,
  "envelope_semi_major_nm": 156.2,
  "envelope_semi_minor_nm": 62.5,
  "envelope_heading_degrees": 45.0,
  "confidence_ellipse_geojson": { "type": "Polygon", "coordinates": [[...]] }
}
```

### `velocity_plausibility_ratio`

`actual_gap_distance_nm / max_plausible_distance_nm`. Values near 1.0 mean the vessel barely had time to travel between its last and next AIS positions at maximum plausible speed — suspicious. Values above 1.0 are physically impossible.

### `impossible_speed_flag`

Boolean. Set `true` when the implied speed between the last pre-gap AIS point and the first post-gap AIS point exceeds **36 knots** — well above the maximum practical speed for any commercial vessel. Strong indicator of position spoofing or MMSI re-use.

---

## OpenAPI / Postman Import

1. Download the spec while the server is running:

```bash
curl -s http://localhost:8000/openapi.json -o radiancefleet_openapi.json
```

2. In Postman: **Import** → **File** → select `radiancefleet_openapi.json`. Postman will generate a full collection with all endpoints, example bodies, and query parameter documentation.

For Insomnia: **Application** → **Import** → select the same JSON file.

---

## Links

- [API.md](API.md) — full endpoint reference
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI spec: http://localhost:8000/openapi.json
