# Troubleshooting

This document covers the most common problems encountered when running
RadianceFleet. For general usage, see [docs/quickstart.md](quickstart.md).
For scoring configuration, see [docs/CUSTOMIZATION.md](CUSTOMIZATION.md).

---

## Setup and infrastructure

### "Docker won't start" / database container exits immediately

Check whether port 5432 is already in use by a host PostgreSQL instance:

```bash
lsof -i :5432
```

If a process is listed, either stop the host instance or edit `docker-compose.yml`
to map to a different host port (e.g. `"5433:5432"`), then update `DATABASE_URL`
in `backend/.env` accordingly.

Also verify the Docker daemon is running:

```bash
docker info
```

If Docker is running but the container still exits, check the container logs:

```bash
docker compose logs db
```

Common causes: insufficient disk space for the PostGIS data volume, or a
corrupted volume from a previous unclean shutdown. To reset the volume:

```bash
docker compose down -v   # WARNING: destroys all database data
docker compose up -d
```

### "PostgreSQL connection refused"

After starting the container, wait for the health check to pass before running
any CLI commands:

```bash
docker compose ps   # STATUS column should show "healthy"
```

If it stays "starting" for more than 60 seconds, check the logs:

```bash
docker compose logs db --tail 30
```

Also verify that `DATABASE_URL` in `backend/.env` matches the docker-compose
service definition:

```bash
# Correct default for the Docker setup
DATABASE_URL=postgresql+psycopg2://radiancefleet:radiancefleet@localhost:5432/radiancefleet
```

### "Frontend shows blank page"

1. Confirm `npm install` was run inside `frontend/`:

   ```bash
   cd frontend && npm install && npm run dev
   ```

2. Open the browser developer console (F12). Look for:
   - **CORS errors** — the backend must be running on port 8000 before the
     frontend is opened. Start the API first: `cd backend && uv run radiancefleet serve`
   - **404 on `/api/v1/...`** — the Vite proxy target may not match the backend
     port. Check `frontend/vite.config.ts` and confirm the proxy target is
     `http://localhost:8000`.
   - **Module not found errors** — re-run `npm install`.

---

## Ingestion and detection

### "No alerts found after ingestion"

Work through this checklist in order:

**1. Did you import corridors?**

The gap detector only creates alerts for vessels that intersect a known
corridor. Without corridors, no alerts are generated.

```bash
radiancefleet corridors import config/corridors.yaml
```

Verify the import succeeded:

```bash
curl http://localhost:8000/api/v1/corridors | python3 -m json.tool | grep '"name"'
```

You should see 11 corridor names.

**2. Did you run gap detection with a wide enough date range?**

The default date range is the last 30 days. If your AIS data predates this
window, explicitly specify the range:

```bash
radiancefleet detect-gaps --from 2020-01-01 --to 2024-12-31
```

**3. Does your data contain vessel types that pass the filter?**

The gap detector only processes tankers and bulk carriers by default
(`ship_type` values: `tanker`, `bulk carrier`, `crude oil tanker`,
`chemical tanker`, `product tanker`, `general cargo`). Vessels with
`ship_type = NULL` or unrecognised types are skipped.

Check what vessel types are in your database:

```bash
curl http://localhost:8000/api/v1/vessels?limit=50 | python3 -m json.tool | grep ship_type
```

If all `ship_type` values are null, your AIS CSV may not include a ship type
column, or the column may be named differently. See CONTRIBUTING.md section 8b
for how to map custom CSV column names.

**4. Are your corridors and AIS positions in compatible coordinate systems?**

All geometries must use WGS84 (EPSG:4326). If you have AIS data in a projected
CRS (e.g. UTM), re-project before ingesting.

### "Watchlist import fails"

The OFAC SDN XML format has changed several times. If `watchlist import --source ofac`
fails with a parse error, check whether the OFAC SDN file you downloaded is in
XML or CSV format:

- **CSV format** (recommended): `sdn.csv` from the OFAC website.
  Use `--source ofac` with the CSV path.
- **XML format**: if you have the legacy XML file, check the field selectors
  in `backend/app/modules/watchlist_loader.py` — the `<sdnEntry>` → `<uid>`
  and `<lastName>` paths occasionally change between OFAC schema versions.

If only some vessels fail to match, this is expected — the fuzzy matcher uses
an 85% similarity threshold. Vessels with very short names or names that differ
significantly from the AIS-reported name will not match automatically. Manual
import via the API is available for individual MMSI overrides.

---

## Scoring and alerts

### "Score too low for a vessel I know is suspicious"

First, check what signals did fire for that vessel:

```bash
radiancefleet list-alerts --min-score 1 --vessel <mmsi>
```

The output includes a `breakdown` field showing which signals contributed and
how many points each awarded.

Common reasons a score is unexpectedly low:

- **Gap is in a dark zone**: Gaps inside corridors with `is_jamming_zone: true`
  receive a -10 adjustment. This is intentional — see the Strait of Hormuz and
  Black Sea/Crimea dark zones in `config/corridors.yaml`.
- **Vessel size multiplier**: Panamax vessels (DWT 60,000–80,000) receive a 0.8x
  multiplier. A raw subtotal of 50 becomes 40 after this multiplier.
- **Legitimacy signals are firing**: If the vessel has a clean 90-day history
  or consistent EU port calls, negative scores may offset positive ones.
- **Corridor correlation did not run**: If `correlate-corridors` was not run
  after `detect-gaps`, the `corridor_factor` defaults to 1.0.

To tune weights for your specific context, see [docs/CUSTOMIZATION.md](CUSTOMIZATION.md).

### "Evidence export blocked"

Export requires alert status to be `under_review` or higher — exporting from
`new` status is intentionally blocked to enforce analyst review (NFR7).

Update the status in the web UI (Alert Detail page → status dropdown), or via
the API:

```bash
curl -X PATCH http://localhost:8000/api/v1/alerts/<id>/status \
  -H "Content-Type: application/json" \
  -d '{"status": "under_review"}'
```

Then export:

```bash
radiancefleet export evidence --alert <id> --format md
```

### "rescore-all-alerts is slow"

`rescore-all-alerts` recomputes every alert in the database sequentially. For
databases with thousands of alerts, this can take several minutes. This is
expected behaviour for the MVP — no background job queue is implemented.

To limit rescoring to a specific vessel during development:

```bash
# There is no --vessel filter on rescore-all-alerts yet.
# Use the API to trigger single-alert rescoring:
curl -X POST http://localhost:8000/api/v1/alerts/<id>/rescore
```

---

## Data quality

### "Score is high but satellite imagery shows nothing"

This is the most important QA step. A high score means the AIS data is
anomalous — it does not confirm the vessel was present. Possible explanations:

- AIS gap due to equipment failure, not deliberate deactivation
- GPS jamming in the area during the gap period (check [gpsjam.org](https://gpsjam.org))
- Poor satellite AIS coverage in the area (open ocean gaps are common)
- The corridor polygon covers a legitimate shipping lane as well as an STS zone

Before publishing, follow the checklist in
[docs/overclaiming-guide.md](overclaiming-guide.md) — especially the satellite
imagery verification step.

### "Corridor correlation is not matching vessels that clearly passed through"

The corridor correlator uses `ST_Intersects` on the trajectory between
consecutive AIS positions — not on the gap endpoints alone. If a vessel has
very sparse AIS positions (e.g. one position before and one position after
transiting a corridor), the interpolated straight-line trajectory may miss a
curved corridor polygon.

To diagnose: check the corridor boundaries in `config/corridors.yaml`. If the
corridor polygon is narrow relative to the vessel's AIS position density, widen
the bounding box slightly. Corridor geometries can be edited directly in the
YAML and re-imported:

```bash
radiancefleet corridors import config/corridors.yaml
radiancefleet correlate-corridors
radiancefleet rescore-all-alerts
```
