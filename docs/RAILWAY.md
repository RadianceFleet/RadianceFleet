# Railway Multi-Service Deployment

RadianceFleet uses three Railway services from a single repository:

1. **web** — FastAPI HTTP server
2. **ws-worker** — Continuous aisstream.io WebSocket consumer
3. **cron-updater** — Hourly batch updater (watchlists, detection, notifications)

All three share the same Docker image and database.

---

## Prerequisites

- Railway project with a **Postgres** plugin (or external DB via `DATABASE_URL`)
- Docker image published to a registry, or Railway building from the repo

---

## Service Configuration

### 1. web (existing)

Already configured in `railway.toml`.

| Setting | Value |
|---------|-------|
| Start command | `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers` |
| Health check path | `/health` |
| Env vars | `DATABASE_URL=${{Postgres.DATABASE_URL}}`, `ADMIN_PASSWORD`, `SECRET_KEY` |

### 2. ws-worker

Create via Railway dashboard: **New Service → Same Repo**.

| Setting | Value |
|---------|-------|
| Start command | `uv run radiancefleet stream --batch-interval 30` |
| Health check path | *(leave empty — not an HTTP server)* |
| Restart policy | `ON_FAILURE` |
| Env vars | `DATABASE_URL=${{Postgres.DATABASE_URL}}`, `AISSTREAM_API_KEY` |

> **Note**: `WORKDIR` is already `/app/backend` from the Dockerfile — no `cd` needed.

Railway sends **SIGTERM** on redeployment. The `stream` command handles this
gracefully, writing a `stopped` heartbeat before exiting.

### 3. cron-updater

Create via Railway dashboard: **New Service → Same Repo**.

| Setting | Value |
|---------|-------|
| Start command | `uv run radiancefleet update --days 90` |
| Cron schedule | `0 * * * *` (hourly) |
| Restart policy | `ON_FAILURE` |
| Env vars | `DATABASE_URL=${{Postgres.DATABASE_URL}}`, `AISSTREAM_WORKER_ENABLED=true` |

Setting `AISSTREAM_WORKER_ENABLED=true` prevents the `CollectionScheduler`
from duplicating AIS collection — the ws-worker already handles it.

> **Important**: The cron process must exit cleanly after completion. Railway
> skips the next scheduled run if the previous one is still active.

---

## Shared Environment Variables

Use Railway **reference variables** to share the database URL:

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Reference variables are **not auto-shared** between services — you must set
them on each service individually.

---

## Verifying Workers

After deploying all three services, check the worker health endpoint:

```bash
curl https://your-app.up.railway.app/api/v1/health/workers | jq .
```

Expected response when everything is healthy:

```json
{
  "workers": {
    "ws-worker": {
      "status": "running",
      "last_heartbeat_utc": "2026-03-10T12:00:00",
      "heartbeat_age_seconds": 15,
      "stale": false,
      "records_processed": 4200
    },
    "cron-updater": {
      "status": "idle",
      "last_heartbeat_utc": "2026-03-10T11:00:00",
      "heartbeat_age_seconds": 3615,
      "stale": false,
      "records_processed": 0
    }
  },
  "worker_count": 2,
  "expected_workers": ["ws-worker"],
  "missing_workers": [],
  "state": "healthy",
  "data_flowing": true,
  "latest_ais_utc": "2026-03-10T12:00:00",
  "all_healthy": true
}
```

### Troubleshooting

| Symptom | Likely Cause |
|---------|-------------|
| `worker_count: 0` | Workers haven't started or can't reach DB |
| `stale: true` on ws-worker | WebSocket disconnected or circuit breaker open |
| `data_flowing: false` | No AIS data in last 5 minutes — check API key |
| `status: "error"` | Check `error_message` field for details |
| `missing_workers` not empty | Expected worker hasn't sent first heartbeat |
