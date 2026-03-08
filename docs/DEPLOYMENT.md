# Deployment Guide

This guide covers five deployment scenarios:

1. **[Docker Hub / ghcr.io (Pre-built Image)](#docker-hub--ghcrio-pre-built-image)** — fastest path for Docker users
2. **[Hosted Public Instance (Railway + Cloudflare Pages)](#hosted-public-instance-railway--cloudflare-pages)** — recommended for a publicly accessible, continuously updated instance
3. **[Local Development (SQLite, no Docker)](#local-development-sqlite-no-docker)** — fastest path for a single analyst on a laptop
4. **[Single-User Production (Docker Compose + PostgreSQL)](#single-user-production-docker-compose--postgresql)** — dedicated private server
5. **[Multi-User Production](#multi-user-production)** — shared team server behind nginx

---

## Docker Hub / ghcr.io (Pre-built Image)

Pre-built images are published on every push to `main`:

```bash
# From Docker Hub
docker pull radiancefleet/radiancefleet:latest

# Or from GitHub Container Registry
docker pull ghcr.io/radiancefleet/radiancefleet:latest
```

Run with SQLite (simplest):

```bash
docker run -p 8000:8000 radiancefleet/radiancefleet:latest
```

Run with PostgreSQL:

```bash
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql://user:pass@host:5432/radiancefleet \
  radiancefleet/radiancefleet:latest
```

Images are tagged with both `latest` and the git commit SHA (e.g., `radiancefleet/radiancefleet:abc1234`) for reproducible deployments.

---

## Hosted Public Instance (Railway + Cloudflare Pages)

Deploy RadianceFleet as a public-facing service with live data updated every 4 hours.
No server admin required — Railway manages the backend and Postgres, Cloudflare Pages serves
the frontend from global CDN.

**Estimated cost: ~$12–25/month** (Railway web + Postgres + cron, Cloudflare Pages free).

### Architecture

```
[Cloudflare Pages]  — React frontend, free CDN, global edge
        |
        VITE_API_URL ──► [Railway Web Service]  — FastAPI, $5–10/month
                                 |
                          [Railway Postgres]     — PostgreSQL 16, $5–10/month

[Railway Cron]  — runs every 4 hours:
        radiancefleet update --stream-time 1h
        (watchlists + AIS collection + detection + email notifications)

[Railway WebSocket Worker (optional)]  — runs continuously:
        radiancefleet stream
        (set AISSTREAM_WORKER_ENABLED=true on cron to avoid overlap)
```

### Step 1 — Fork and connect to Railway

1. Fork the repository on GitHub.
2. Create a Railway account at railway.app and start a new project.
3. Add a **New Service → GitHub Repo** — point it at your fork, source directory `backend`.
4. Add a **Postgres** plugin addon to the project.
5. Add a second **New Service → GitHub Repo** for the cron worker (same repo, same source `backend`).

### Step 2 — Configure environment variables

In the Railway **web service** environment tab, set:

```dotenv
# Database — use the Railway Postgres addon URL
DATABASE_URL=${{Postgres.DATABASE_URL}}

# Config paths
CORRIDORS_CONFIG=../config/corridors.yaml
RISK_SCORING_CONFIG=../config/risk_scoring.yaml

# Admin auth — generates a JWT for the admin login; replaces RADIANCEFLEET_API_KEY
# Generate with: openssl rand -hex 32
ADMIN_JWT_SECRET=your_256bit_random_secret_here
ADMIN_PASSWORD=your_strong_admin_password_here

# IMPORTANT: Do NOT set RADIANCEFLEET_API_KEY — it blocks all public GET requests
# RADIANCEFLEET_API_KEY=  ← leave unset

# CORS: add your Cloudflare Pages domain
CORS_ORIGINS=https://your-app.pages.dev,https://your-domain.com

# Public URL (used in email links)
PUBLIC_URL=https://your-domain.com

# Email notifications (optional — get key at resend.com)
RESEND_API_KEY=re_your_key_here
EMAIL_FROM_DOMAIN=your-domain.com

# AIS data sources (set whichever you have access to)
GFW_API_TOKEN=your_gfw_token
AISSTREAM_API_KEY=your_aisstream_key
KYSTVERKET_ENABLED=true
DIGITRAFFIC_ENABLED=true
CREA_ENABLED=true
```

In the Railway **cron service**, set the same environment variables plus:
```dotenv
# No additional vars needed — uses same DATABASE_URL via shared Postgres addon
```

### Step 3 — Configure the start command

In the Railway **web service** settings, set:

```
Start Command: uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers
```

The `--proxy-headers` flag is required so rate limiting reads real client IPs from
Railway's `X-Forwarded-For` header rather than collapsing all traffic to a single proxy IP.

In the Railway **cron service** settings, set:

```
Cron Schedule: 0 */4 * * *
Start Command: radiancefleet update --stream-time 1h
```

### Step 4 — First-time database setup

After the web service first deploys, open the Railway shell or run via CLI:

```bash
radiancefleet start
```

This initialises the database schema, seeds ports, imports corridor configuration, runs
an initial AIS collection pass, and executes the full detection pipeline. It is safe to
run only once; subsequent updates use `radiancefleet update`.

To load sample data without API keys (for testing the deployment):

```bash
radiancefleet start --demo
```

### Step 5 — Deploy the frontend to Cloudflare Pages

1. In the Cloudflare Pages dashboard, create a new project connected to your GitHub fork.
2. Set build configuration:
   - **Framework preset:** Vite
   - **Build command:** `npm run build`
   - **Build output directory:** `dist`
   - **Root directory:** `frontend`
3. Set environment variable:
   ```
   VITE_API_URL=https://your-app.railway.app
   ```
4. Deploy. Cloudflare Pages will build and serve the frontend from global edge nodes.

### Step 6 — Verify the deployment

```bash
# Backend health (replace with your Railway URL)
curl https://your-app.railway.app/health
# Expected: {"status": "ok", "version": "3.2.0", "circuit_breakers": {...}}

# Data freshness (shows last update timestamp)
curl https://your-app.railway.app/api/v1/health/data-freshness

# Frontend — open in browser
https://your-app.pages.dev
```

### Step 7 — Admin login

The admin UI controls write operations (importing data, running detection, corridor
management). From the frontend:

1. No visible admin controls appear for public users.
2. To access admin mode, navigate to `/login` or click the admin login button (visible only
   in the nav bar to logged-in users).
3. Log in with the `ADMIN_PASSWORD` set in Railway environment variables.
4. Tokens expire after 30 minutes; re-login as needed.

### Ongoing operations

The Railway cron service runs `radiancefleet update --stream-time 1h` every 4 hours
automatically. This fetches latest watchlists, collects AIS data from all enabled sources,
runs detection, and sends email alert notifications to confirmed subscribers.

To trigger a manual update:

```bash
# Via Railway shell or CLI
radiancefleet update --stream-time 30m
```

### Cost breakdown

| Service | Cost |
|---------|------|
| Railway web service | ~$5–10/month |
| Railway Postgres addon | ~$5–10/month |
| Railway cron service | ~$2–5/month |
| Cloudflare Pages | Free |
| **Total** | **~$12–25/month** |

For sustained funding, see the `/donate` page in the app or apply to:
- **IJ4EU** — EU cross-border journalism fund, up to €50K
- **NED** — National Endowment for Democracy media freedom grants

---

## Local Development (SQLite, no Docker)

This path is the fastest way to run RadianceFleet on a laptop without any external services.
SQLite is fully supported — all geometry is stored as WKT text and processed via Shapely in
Python. No PostGIS or Docker required.

### Prerequisites

| Tool | Minimum version | Notes |
|---|---|---|
| Python | 3.12 | |
| uv | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js | 18 | For the frontend only |
| Git | any | |

```bash
git clone https://github.com/your-org/RadianceFleet
cd RadianceFleet

# Install backend dependencies into an isolated virtual environment
cd backend
uv sync

# Activate the virtual environment
source .venv/bin/activate

# Initialise the database schema
DATABASE_URL=sqlite:///./rf.db radiancefleet init-db

# Load the seed corridor configuration
DATABASE_URL=sqlite:///./rf.db radiancefleet corridors import ../config/corridors.yaml
```

Start the API server:

```bash
DATABASE_URL=sqlite:///./rf.db uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Start the frontend development server (in a separate terminal):

```bash
cd frontend
npm install
npm run dev
```

The frontend will be available at `http://localhost:5173`. The API will be at
`http://localhost:8000`. The Vite dev server proxies API requests automatically.

Ingest sample data to verify the pipeline:

```bash
DATABASE_URL=sqlite:///./rf.db radiancefleet ingest ais ../data/sample.csv
DATABASE_URL=sqlite:///./rf.db radiancefleet detect-gaps
DATABASE_URL=sqlite:///./rf.db radiancefleet score-alerts
```

---

## Single-User Production (Docker Compose + PostgreSQL)

This is the recommended setup for a single analyst working on a dedicated machine or a
private server with no external network exposure.

### Process Topology

```
┌─────────────┐    ┌─────────────┐    ┌──────────────────────┐
│  web        │    │  cron       │    │  ws-worker (optional) │
│  FastAPI    │    │  update     │    │  stream               │
│  port 8000  │    │  every 4h   │    │  continuous           │
└──────┬──────┘    └──────┬──────┘    └──────────┬───────────┘
       │                  │                      │
       └──────────────────┼──────────────────────┘
                          │
                   ┌──────┴──────┐
                   │  db         │
                   │  PostgreSQL │
                   └─────────────┘
```

- **Without ws-worker (default):** `docker compose up -d` — cron handles aisstream in time-boxed windows
- **With ws-worker:** `AISSTREAM_WORKER_ENABLED=true docker compose --profile worker up -d` — continuous ingestion

### Step 1 — Clone and configure the environment

```bash
git clone https://github.com/your-org/RadianceFleet
cd RadianceFleet

cp .env.example backend/.env
```

Open `backend/.env` in a text editor and set at minimum:

```dotenv
DATABASE_URL=postgresql+psycopg2://radiancefleet:CHANGE_THIS_PASSWORD@localhost:5432/radiancefleet

CORRIDORS_CONFIG=../config/corridors.yaml
RISK_SCORING_CONFIG=../config/risk_scoring.yaml

LOG_LEVEL=INFO
GAP_MIN_HOURS=2.0
GAP_ALERT_HOURS=6.0
STS_PROXIMITY_METERS=200.0
STS_MIN_WINDOWS=8
```

Also update the `POSTGRES_PASSWORD` in `docker-compose.yml` to match what you set in
`DATABASE_URL`. Keep `docker-compose.yml` out of version control if it contains real passwords,
or use a `docker-compose.override.yml` for local secrets.

### Step 2 — Start PostgreSQL and PostGIS

```bash
docker compose up -d postgres
```

Wait for the health check to pass (the container runs `pg_isready` every 10 seconds):

```bash
docker compose ps
# postgres service should show "healthy"
```

### Step 3 — Install backend dependencies

```bash
cd backend
uv sync
source .venv/bin/activate
```

### Step 4 — Initialise the database schema

```bash
radiancefleet init-db
```

This creates all tables and PostGIS extensions. It is idempotent and safe to re-run.

### Step 5 — Load corridor configuration

```bash
radiancefleet corridors import config/corridors.yaml
```

This loads the 11 seed corridors (export routes, STS zones, dark zones) that gate corridor
correlation. Gap and spoofing detectors work without corridors, but corridor correlation
signals will be absent from risk scores until this step is complete.

### Step 6 — Build the frontend

For a one-off production build (output goes to `frontend/dist/`):

```bash
cd frontend
npm install
npm run build
```

Serve the built files with any static file server, or point nginx at `frontend/dist/`.

For development mode with hot reload (acceptable for a single-user local setup):

```bash
npm run dev
```

### Step 7 — Start the API server

```bash
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Or use the Makefile shortcut from the project root:

```bash
make dev
```

### Step 8 — Ingest your first AIS file

```bash
radiancefleet ingest ais /path/to/your/ais_data.csv
```

Then run the detection pipeline:

```bash
radiancefleet detect-gaps
radiancefleet detect-spoofing
radiancefleet detect-loitering
radiancefleet detect-sts
radiancefleet correlate-corridors
radiancefleet score-alerts
```

Or use the Makefile targets individually:

```bash
make detect-gaps
make score
```

### Step 9 — Verify the deployment

```bash
curl http://localhost:8000/api/v1/health
# Expected: {"status": "ok", "db": "connected"}
```

Open `http://localhost:5173` (dev mode) or your static file server URL in a browser.

---

## Multi-User Production

For a small team of analysts sharing a single RadianceFleet instance on a server.

### Architecture

```
Internet / analyst browser
        |
   nginx (TLS + bearer token auth)
        |
   FastAPI API (127.0.0.1:8000)
        |
   PostgreSQL + PostGIS (127.0.0.1:5432)
```

The frontend build artifacts are served directly by nginx as static files. The API is
proxied through nginx and protected by a bearer token.

### nginx configuration

Generate a strong shared token before configuring nginx:

```bash
openssl rand -hex 32
# Example output: a3f8c2d1e4b5...  (keep this secret)
```

Create `/etc/nginx/sites-available/radiancefleet`:

```nginx
server {
    listen 80;
    server_name radiancefleet.yourdomain.example;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name radiancefleet.yourdomain.example;

    ssl_certificate     /etc/ssl/certs/radiancefleet.crt;
    ssl_certificate_key /etc/ssl/private/radiancefleet.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Serve the frontend build
    root /var/www/radiancefleet;
    index index.html;

    location / {
        try_files $uri /index.html;
    }

    # Proxy API requests; require bearer token
    location /api/ {
        if ($http_authorization != "Bearer YOUR_SECRET_TOKEN_HERE") {
            return 401 '{"detail":"Unauthorized"}';
        }
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        # Required for CSV bulk export (StreamingResponse)
        proxy_buffering    off;
        proxy_read_timeout 120s;
    }
}
```

Enable and reload:

```bash
ln -s /etc/nginx/sites-available/radiancefleet /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Copy the frontend build to the document root:

```bash
cp -r /path/to/RadianceFleet/frontend/dist/* /var/www/radiancefleet/
```

Update the frontend's API base URL before building so it points to the production hostname.
Set `VITE_API_BASE_URL=https://radiancefleet.yourdomain.example` in `frontend/.env.production`
and rebuild.

### Concurrency considerations

RadianceFleet uses synchronous SQLAlchemy with a single database session per request.
v3.3 adds three concurrency mechanisms to prevent data conflicts:

- **Edit locks**: DB-level advisory locks with configurable TTL (`EDIT_LOCK_TTL_SECONDS`, default 300s). An analyst must acquire a lock before editing an alert; other analysts see a 409 Conflict.
- **Optimistic locking**: A `version` field on gap events is incremented on each update. If two analysts submit changes to the same alert, the second gets a 409 Conflict and must reload.
- **Alert assignment**: Assign alerts to specific analysts to partition work.

Additional recommendations:
- Coordinate before running `rescore-all-alerts`, which rewrites scores for the entire dataset.
- Set a PostgreSQL connection pool size appropriate for your team size:

```dotenv
# backend/.env
SQLALCHEMY_POOL_SIZE=5
SQLALCHEMY_MAX_OVERFLOW=10
```

---

## Environment Variables Reference

All variables are loaded from `backend/.env` or from the process environment. The `.env` file
takes precedence over defaults; process environment variables take precedence over `.env`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes | — | SQLAlchemy connection string. PostgreSQL: `postgresql+psycopg2://user:pass@host:port/db`. SQLite: `sqlite:///./rf.db` |
| `CORRIDORS_CONFIG` | No | `../config/corridors.yaml` | Path to corridor YAML, relative to `backend/` |
| `RISK_SCORING_CONFIG` | No | `../config/risk_scoring.yaml` | Path to scoring weights YAML |
| `LOG_LEVEL` | No | `INFO` | Python logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `GAP_MIN_HOURS` | No | `2.0` | Minimum AIS silence duration to record as a gap event |
| `GAP_ALERT_HOURS` | No | `6.0` | Gap duration that triggers an alert |
| `STS_PROXIMITY_METERS` | No | `200.0` | Maximum vessel separation to count as an STS proximity window |
| `STS_MIN_WINDOWS` | No | `8` | Minimum number of proximity windows to classify as an STS event |
| `SQLALCHEMY_POOL_SIZE` | No | `5` | Database connection pool size (PostgreSQL only) |
| `SQLALCHEMY_MAX_OVERFLOW` | No | `10` | Maximum overflow connections above pool size |
| `CAPELLA_API_KEY` | No | — | Capella Space satellite tasking API key |
| `PLANET_API_KEY` | No | — | Planet Labs API key |
| `UMBRA_API_KEY` | No | — | Umbra Space API key |
| `OPENSANCTIONS_API_KEY` | No | — | OpenSanctions API key for watchlist updates |
| `AISSTREAM_API_KEY` | No | — | AISStream.io API key for live AIS ingestion |
| `AISSTREAM_WORKER_ENABLED` | No | `false` | When `true`, cron skips aisstream collection (dedicated ws-worker handles it) |
| `EDIT_LOCK_TTL_SECONDS` | No | `300` | Edit lock duration in seconds before automatic expiry |
| `MAXAR_API_KEY` | No | — | Maxar satellite imagery API key (stub — not yet implemented) |
| `SATELLITE_MONTHLY_BUDGET_USD` | No | `2000.0` | Monthly budget cap for commercial satellite orders |
| `SATELLITE_ORDER_AUTO_SUBMIT` | No | `false` | When `true`, orders are auto-submitted (default: stay in draft for analyst review) |

CORS configuration: if you serve the frontend from a different origin than the API, set
`CORS_ORIGINS` as a comma-separated list in `.env`:

```dotenv
CORS_ORIGINS=http://localhost:5173,https://radiancefleet.yourdomain.example
```

---

## Database Persistence

The Docker volume `postgres_data` stores all PostgreSQL data. It persists across
`docker compose down` / `docker compose up` cycles.

To stop the stack without deleting data:

```bash
docker compose down
```

To stop the stack AND delete all data (destructive, cannot be undone):

```bash
docker compose down -v
```

To inspect the volume:

```bash
docker volume inspect radiancefleet_postgres_data
```

---

## pgAdmin (Debug Profile)

pgAdmin is included for database inspection during development. It is **not started by default**
and requires an explicit profile flag:

```bash
docker compose --profile debug up -d
```

Access at `http://localhost:5050`.

Default credentials (change these if exposing to a network):

- Email: `admin@radiancefleet.local`
- Password: `admin`

Configure a server connection inside pgAdmin:

- Host: `postgres` (the Docker service name, resolved within the Docker network)
- Port: `5432`
- Database: `radiancefleet`
- Username: `radiancefleet`
- Password: your `POSTGRES_PASSWORD`

Do not run the debug profile in production.

---

## Backup Strategy

### Creating a backup

Run a logical dump from the running container:

```bash
docker exec radiancefleet_db pg_dump -U radiancefleet radiancefleet > backup_$(date +%Y%m%d).sql
```

Store the backup file outside the Docker volume, ideally on encrypted storage or a separate
host. For automated daily backups, add a cron entry:

```cron
0 2 * * * docker exec radiancefleet_db pg_dump -U radiancefleet radiancefleet > /backups/rf_$(date +\%Y\%m\%d).sql
```

Run a backup before every major ingestion run. AIS data ingestion is additive and
non-reversible without a restore.

### Restoring from a backup

```bash
# Ensure the database exists and is empty, or drop and recreate it
docker exec -i radiancefleet_db psql -U radiancefleet radiancefleet < backup_20240101.sql
```

Test restores periodically. A backup that has never been tested is not a backup.

---

## Health Check

The API exposes a health endpoint that verifies both the server and the database connection:

```bash
curl http://localhost:8000/api/v1/health
```

Expected response:

```json
{"status": "ok", "db": "connected"}
```

Include this URL in any uptime monitoring or systemd watchdog configuration. If the database
is unreachable, the response will be `{"status": "degraded", "db": "error"}` with HTTP 503.

For Docker-based deployments, add a healthcheck to the API service in `docker-compose.yml`:

```yaml
  api:
    # ... other config ...
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

---

## WebSocket Worker (Continuous AIS Ingestion)

By default, the `cron` service runs `radiancefleet update --stream-time 1h`, which
time-boxes the aisstream.io WebSocket to 1 hour per 4-hour cycle — leaving ~3 hours
of dead time with no AIS ingestion.

The optional `ws-worker` service runs `radiancefleet stream` continuously, eliminating
dead time.

### When to use the worker

- **Time-boxed (default):** Sufficient for daily triage workflows where gaps of a few
  hours between ingestion cycles are acceptable.
- **Continuous (ws-worker):** Required when real-time alerting matters or when monitoring
  vessels transiting narrow corridors where a 3-hour gap could miss an event entirely.

### How to enable

```bash
# Set env var so cron doesn't also run aisstream
export AISSTREAM_WORKER_ENABLED=true

# Start all services including ws-worker
docker compose --profile worker up -d
```

### Monitoring

The worker reports ingestion status with source `aisstream-worker`. Check health via:

```
GET /api/v1/health/data-freshness
```

Look for the `aisstream-worker` entry — `last_success` should be within the last
few minutes if the worker is running.

### Circuit breaker behavior

The worker has three layers of resilience:

1. **Inner retries:** `stream_ais()` retries transient WebSocket failures (3 attempts
   with exponential backoff).
2. **Outer retry loop:** When the circuit breaker opens (too many failures), the CLI
   waits 60 seconds (breaker reset timeout) and reconnects automatically.
3. **Docker restart:** `restart: unless-stopped` handles unexpected crashes (OOM,
   segfault). The inner retry logic handles normal transient failures.

---

## Troubleshooting

### PostgreSQL won't connect

1. Check container status: `docker compose ps` — the postgres service must show `healthy`.
2. If status is `starting`, wait for the health check interval (10s) and try again.
3. If status is `unhealthy`, check logs: `docker compose logs postgres`.
4. Verify `DATABASE_URL` in `backend/.env` matches the credentials in `docker-compose.yml`.
5. If you changed the password in `docker-compose.yml` after the volume was already created,
   the password in the volume's data directory will not match. Drop and recreate the volume:
   `docker compose down -v && docker compose up -d postgres`.

### No alerts found after ingestion

1. Verify corridors are loaded: `radiancefleet corridors import config/corridors.yaml` (safe
   to re-run).
2. Check the date range of your AIS data. Gap detection requires explicit date bounds. Run:
   ```bash
   radiancefleet detect-gaps --from 2020-01-01 --to 2024-12-31
   ```
3. Confirm the ingestion completed without errors: `radiancefleet ingest ais <file>` should
   report row counts. Check `LOG_LEVEL=DEBUG` output for any parsing errors.
4. Run `radiancefleet score-alerts` after detection; alerts exist in the database but scores
   are computed separately.

### Score too low for a vessel I know is suspicious

Risk scores depend on scoring weights in `config/risk_scoring.yaml`. You can inspect and
adjust weights for your use case — see `docs/risk-scoring-config.md` for the weight schema.

To inspect a specific vessel's alerts at any score threshold:

```bash
radiancefleet list-alerts --min-score 1 --vessel <mmsi>
```

To recompute scores after adjusting weights:

```bash
radiancefleet rescore-all-alerts
```

### Frontend shows no data

1. Verify the API is running: `curl http://localhost:8000/api/v1/health`.
2. Check CORS configuration. If the frontend origin is not in `CORS_ORIGINS`, the browser
   will block API responses. Add the frontend URL:
   ```dotenv
   CORS_ORIGINS=http://localhost:5173
   ```
   Restart the API server after changing this value.
3. Open the browser developer console (F12) and check the Network tab for failed requests.
   A `401` response means the bearer token is missing or wrong (multi-user nginx setup only).
   A `CORS` error confirms the CORS_ORIGINS issue above.

### Alembic migration errors

If you see `Table already exists` errors when running `radiancefleet init-db` after a schema
change, use Alembic directly:

```bash
cd backend
alembic upgrade head
```

To generate a new migration after model changes:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```
