# Configuration Reference

All settings are defined in `backend/app/config.py` as a Pydantic `BaseSettings` class. They can be set via environment variables or a `.env` file (searched at `../.env` and `.env` relative to the backend directory).

---

## Core

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `DATABASE_URL` | `str` | `sqlite:///radiancefleet.db` | SQLAlchemy database URL. SQLite by default; Postgres supported. |
| `CORRIDORS_CONFIG` | `str` | `config/corridors.yaml` | Path to corridor definitions (shipping lanes, anchorages). |
| `RISK_SCORING_CONFIG` | `str` | `config/risk_scoring.yaml` | Path to risk scoring weights and thresholds. |
| `LOG_LEVEL` | `str` | `INFO` | Python log level (DEBUG, INFO, WARNING, ERROR). |
| `LOG_FORMAT` | `str` | `text` | Log output format: `text` (console) or `json` (structured/production). |
| `DATA_DIR` | `str` | `data` | Directory for file-based data storage. |
| `PUBLIC_URL` | `str` | `http://localhost:5173` | Public-facing URL, used for CORS and generated links. |

## Database

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `DB_POOL_SIZE` | `int` | `10` | SQLAlchemy connection pool size. |
| `DB_MAX_OVERFLOW` | `int` | `20` | Max connections beyond pool size. |
| `MAX_UPLOAD_SIZE_MB` | `int` | `500` | Maximum file upload size in megabytes. |
| `MAX_QUERY_LIMIT` | `int` | `500` | Maximum number of rows returned by list endpoints. |

## API & Auth

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `RADIANCEFLEET_API_KEY` | `str \| None` | `None` | Public API key for `X-API-Key` header authentication. |
| `CORS_ORIGINS` | `str` | `http://localhost:5173` | Comma-separated allowed CORS origins. |
| `ADMIN_JWT_SECRET` | `str \| None` | `None` | Secret for signing JWT tokens. Generate with `openssl rand -hex 32`. Required if `ADMIN_PASSWORD` is set. |
| `ADMIN_PASSWORD` | `str \| None` | `None` | Password for `POST /admin/login`. |
| `EDIT_LOCK_TTL_SECONDS` | `int` | `300` | Alert edit lock timeout (seconds). |
| `RATE_LIMIT_VIEWER` | `str` | `30/minute` | Rate limit for unauthenticated/viewer requests. |
| `RATE_LIMIT_ADMIN` | `str` | `120/minute` | Rate limit for admin endpoints. |
| `RATE_LIMIT_DEFAULT` | `str` | `60/minute` | Default rate limit for all other endpoints. |
| `SSE_MAX_CONNECTIONS` | `int` | `20` | Maximum concurrent SSE connections. |

> **Validation rule:** If `ADMIN_PASSWORD` is set, `ADMIN_JWT_SECRET` must also be set or startup will fail.

## Detection Thresholds

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `GAP_MIN_HOURS` | `float` | `2.0` | Minimum AIS gap duration to record. |
| `GAP_ALERT_HOURS` | `float` | `6.0` | AIS gap duration that triggers an alert. |
| `STS_PROXIMITY_METERS` | `float` | `200.0` | Ship-to-ship transfer proximity threshold (meters). |
| `STS_MIN_WINDOWS` | `int` | `8` | Minimum sustained proximity windows for STS (8 x 15 min = 2 hours). |
| `CLASS_B_NOISE_FILTER_SECONDS` | `int` | `180` | Ignore duplicate Class B transmissions within this window. |
| `LOITER_GAP_LINKAGE_HOURS` | `int` | `48` | Link loitering events to AIS gaps within this window. |
| `ANCHORAGE_TOLERANCE_DEG` | `float` | `0.05` | Bounding-box tolerance for anchorage corridors (~5.5 km). |
| `FUZZY_MATCH_THRESHOLD` | `int` | `85` | Minimum fuzzy string match score for vessel name matching. |
| `COVERAGE_CONFIG` | `str` | `config/coverage.yaml` | Path to coverage quality zone definitions. |

## AIS Data Sources

### AISStream.io (real-time WebSocket)

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `AISSTREAM_API_KEY` | `str \| None` | `None` | API key for aisstream.io. |
| `AISSTREAM_WS_URL` | `str` | `wss://stream.aisstream.io/v0/stream` | WebSocket endpoint. |
| `AISSTREAM_BATCH_INTERVAL` | `int` | `30` | Batch insert interval (seconds). |
| `AISSTREAM_DEFAULT_DURATION` | `int` | `3600` | Default stream duration (seconds). |
| `AISSTREAM_WORKER_ENABLED` | `bool` | `False` | Enable background AISStream worker. |

### Regional & Public Feeds

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `DIGITRAFFIC_ENABLED` | `bool` | `True` | Enable Finnish Digitraffic AIS feed. |
| `AISHUB_USERNAME` | `str \| None` | `None` | AISHub account username. |
| `AISHUB_ENABLED` | `bool` | `False` | Enable AISHub feed. |
| `KYSTVERKET_ENABLED` | `bool` | `True` | Enable Norwegian Kystverket AIS TCP stream. |
| `KYSTVERKET_HOST` | `str` | `153.44.253.27` | Kystverket TCP host. |
| `KYSTVERKET_PORT` | `int` | `5631` | Kystverket TCP port. |
| `DMA_ENABLED` | `bool` | `True` | Enable Danish Maritime Authority historical AIS. |
| `BARENTSWATCH_ENABLED` | `bool` | `False` | Enable BarentsWatch AIS REST API. |
| `BARENTSWATCH_CLIENT_ID` | `str` | `""` | OAuth2 client ID for BarentsWatch. |
| `BARENTSWATCH_CLIENT_SECRET` | `str` | `""` | OAuth2 client secret for BarentsWatch. |
| `BARENTSWATCH_TOKEN_URL` | `str` | `https://id.barentswatch.no/connect/token` | BarentsWatch OAuth2 token endpoint. |
| `BARENTSWATCH_API_URL` | `str` | `https://live.ais.barentswatch.no/api` | BarentsWatch AIS API base URL. |
| `NOAA_BASE_URL` | `str` | `https://coast.noaa.gov/htdata/CMSP/AISDataHandler` | NOAA historical AIS base URL. |

### Collection Scheduler

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `COLLECT_DIGITRAFFIC_INTERVAL` | `int` | `1800` | Digitraffic polling interval (seconds, default 30 min). |
| `COLLECT_AISSTREAM_INTERVAL` | `int` | `300` | AISStream polling interval (seconds, default 5 min). |
| `COLLECT_RETENTION_DAYS` | `int` | `90` | Days to keep collected feed data. |
| `DATA_FETCH_TIMEOUT` | `float` | `120.0` | HTTP timeout for data fetches (seconds). |

## Data Retention

Three retention tiers control how long different classes of AIS data are kept:

| Setting | Type | Default | Tier | Description |
|---------|------|---------|------|-------------|
| `AIS_OBSERVATION_RETENTION_HOURS` | `int` | `72` | Raw observations | Raw per-message AIS observations. Purged after 72 hours to control database size. |
| `RETENTION_DAYS_REALTIME` | `int` | `90` | Realtime feeds | Aggregated data from live feeds (Digitraffic, Kystverket, AISStream). |
| `RETENTION_DAYS_HISTORICAL` | `int \| None` | `None` | Historical archives | Data from NOAA, DMA, and other historical sources. `None` means keep forever. |

## External APIs

### Global Fishing Watch

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `GFW_API_TOKEN` | `str \| None` | `None` | API token for Global Fishing Watch. |
| `GFW_API_BASE_URL` | `str` | `https://gateway.api.globalfishingwatch.org` | GFW API base URL. |

### Copernicus CDSE (Sentinel-1 SAR)

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `COPERNICUS_CLIENT_ID` | `str \| None` | `None` | OAuth2 client ID for Copernicus Data Space. |
| `COPERNICUS_CLIENT_SECRET` | `str \| None` | `None` | OAuth2 client secret for Copernicus Data Space. |

### CREA Russia Fossil Tracker

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `CREA_ENABLED` | `bool` | `True` | Enable CREA fossil tracker integration. |
| `CREA_API_BASE_URL` | `str` | `https://api.russiafossiltracker.com` | CREA API base URL. |

### Equasis

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `EQUASIS_USERNAME` | `str \| None` | `None` | Equasis login username. |
| `EQUASIS_PASSWORD` | `str \| None` | `None` | Equasis login password. |
| `EQUASIS_SCRAPING_ENABLED` | `bool` | `False` | Enable Equasis scraping. Opt-in due to ToS. |

### Paid Verification Providers

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `SKYLIGHT_API_KEY` | `str` | `""` | Skylight API key. |
| `SPIRE_API_KEY` | `str` | `""` | Spire Maritime API key. |
| `SEAWEB_API_KEY` | `str` | `""` | SeaWeb API key. |
| `VERIFICATION_MONTHLY_BUDGET_USD` | `float` | `500.0` | Monthly spend cap for paid verification lookups. |

## Vessel Registry APIs

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `DATALASTIC_API_KEY` | `str \| None` | `None` | API key for Datalastic vessel registry enrichment. |

## Satellite Imagery Ordering

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `PLANET_API_KEY` | `str \| None` | `None` | Planet Labs API key. |
| `CAPELLA_API_KEY` | `str \| None` | `None` | Capella Space API key. |
| `MAXAR_API_KEY` | `str \| None` | `None` | Maxar API key (used alongside OAuth2 ROPC). |
| `MAXAR_USERNAME` | `str \| None` | `None` | Maxar OAuth2 username. |
| `UMBRA_CLIENT_ID` | `str \| None` | `None` | Umbra OAuth2 client ID. |
| `UMBRA_API_KEY` | `str \| None` | `None` | Umbra API key. |
| `SATELLITE_MONTHLY_BUDGET_USD` | `float` | `2000.0` | Monthly spend cap for satellite imagery orders. |
| `SATELLITE_ORDER_AUTO_SUBMIT` | `bool` | `False` | Automatically submit satellite orders (if `False`, orders are created in draft). |

## Vessel Identity Merging

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `MERGE_MAX_SPEED_KN` | `float` | `16.0` | Maximum plausible vessel speed (knots) for identity merge candidates. |
| `MERGE_MAX_GAP_DAYS` | `int` | `30` | Maximum gap between identity sightings for merge consideration. |
| `MERGE_AUTO_CONFIDENCE_THRESHOLD` | `int` | `75` | Auto-merge confidence threshold (0-100). |
| `MERGE_CANDIDATE_MIN_CONFIDENCE` | `int` | `50` | Minimum confidence to surface as a merge candidate. |
| `HISTORY_CROSS_REFERENCE_ENABLED` | `bool` | `True` | Cross-reference historical records during merge analysis. |

## Detection Feature Flags

All feature flags default to `True` (enabled). Set to `False` to disable a specific detector or its scoring contribution.

Flags follow a consistent pattern: `*_DETECTION_ENABLED` controls whether the detector runs, and the corresponding `*_SCORING_ENABLED` controls whether its output contributes to the risk score.

| Flag | Controls | Default |
|------|----------|---------|
| `TRACK_NATURALNESS_ENABLED` | Track naturalness analysis | `True` |
| `TRACK_NATURALNESS_SCORING_ENABLED` | Track naturalness scoring contribution | `True` |
| `DRAUGHT_DETECTION_ENABLED` | Draught change detection (laden/ballast) | `True` |
| `DRAUGHT_SCORING_ENABLED` | Draught scoring contribution | `True` |
| `STATELESS_MMSI_DETECTION_ENABLED` | Stateless/unassigned MMSI detection | `True` |
| `STATELESS_MMSI_SCORING_ENABLED` | Stateless MMSI scoring contribution | `True` |
| `FLAG_HOPPING_DETECTION_ENABLED` | Flag state change detection | `True` |
| `FLAG_HOPPING_SCORING_ENABLED` | Flag hopping scoring contribution | `True` |
| `IMO_FRAUD_DETECTION_ENABLED` | IMO number fraud detection | `True` |
| `IMO_FRAUD_SCORING_ENABLED` | IMO fraud scoring contribution | `True` |
| `FEED_OUTAGE_DETECTION_ENABLED` | AIS feed outage detection | `True` |
| `COVERAGE_QUALITY_TAGGING_ENABLED` | Coverage quality zone tagging | `True` |
| `DARK_STS_DETECTION_ENABLED` | Dark ship-to-ship transfer detection | `True` |
| `DARK_STS_SCORING_ENABLED` | Dark STS scoring contribution | `True` |
| `FLEET_ANALYSIS_ENABLED` | Fleet-level behavioral analysis | `True` |
| `FLEET_SCORING_ENABLED` | Fleet analysis scoring contribution | `True` |
| `PI_VALIDATION_DETECTION_ENABLED` | P&I club validation | `True` |
| `PI_VALIDATION_SCORING_ENABLED` | P&I validation scoring contribution | `True` |
| `FRAUDULENT_REGISTRY_DETECTION_ENABLED` | Fraudulent registry detection | `True` |
| `FRAUDULENT_REGISTRY_SCORING_ENABLED` | Fraudulent registry scoring contribution | `True` |
| `STALE_AIS_DETECTION_ENABLED` | Stale AIS data detection | `True` |
| `STALE_AIS_SCORING_ENABLED` | Stale AIS scoring contribution | `True` |
| `AT_SEA_OPERATIONS_SCORING_ENABLED` | At-sea extended operations scoring | `True` |
| `ISM_CONTINUITY_DETECTION_ENABLED` | ISM/P&I continuity gap detection | `True` |
| `ISM_CONTINUITY_SCORING_ENABLED` | ISM continuity scoring contribution | `True` |
| `RENAME_VELOCITY_DETECTION_ENABLED` | Rapid vessel renaming detection | `True` |
| `RENAME_VELOCITY_SCORING_ENABLED` | Rename velocity scoring contribution | `True` |
| `DESTINATION_DETECTION_ENABLED` | Destination field manipulation detection | `True` |
| `DESTINATION_SCORING_ENABLED` | Destination manipulation scoring contribution | `True` |
| `STS_CHAIN_DETECTION_ENABLED` | STS relay chain detection | `True` |
| `STS_CHAIN_SCORING_ENABLED` | STS chain scoring contribution | `True` |
| `SCRAPPED_REGISTRY_DETECTION_ENABLED` | Scrapped vessel registry detection | `True` |
| `SCRAPPED_REGISTRY_SCORING_ENABLED` | Scrapped registry scoring contribution | `True` |
| `TRACK_REPLAY_DETECTION_ENABLED` | Track replay/spoofing detection | `True` |
| `TRACK_REPLAY_SCORING_ENABLED` | Track replay scoring contribution | `True` |
| `MERGE_CHAIN_DETECTION_ENABLED` | MMSI chain detection | `True` |
| `MERGE_CHAIN_SCORING_ENABLED` | MMSI chain scoring contribution | `True` |
| `FINGERPRINT_ENABLED` | Behavioral fingerprinting | `True` |
| `FINGERPRINT_SCORING_ENABLED` | Fingerprint scoring contribution | `True` |
| `SAR_CORRELATION_ENABLED` | Satellite-AIS correlation | `True` |
| `SAR_CORRELATION_SCORING_ENABLED` | SAR correlation scoring contribution | `True` |
| `OWNERSHIP_GRAPH_ENABLED` | Corporate ownership graph analysis | `True` |
| `OWNERSHIP_GRAPH_SCORING_ENABLED` | Ownership graph scoring contribution | `True` |
| `CONVOY_DETECTION_ENABLED` | Convoy / floating storage / Arctic corridor detection | `True` |
| `CONVOY_SCORING_ENABLED` | Convoy scoring contribution | `True` |
| `VOYAGE_PREDICTION_ENABLED` | Voyage prediction | `True` |
| `VOYAGE_SCORING_ENABLED` | Voyage prediction scoring contribution | `True` |
| `CARGO_INFERENCE_ENABLED` | Cargo type inference | `True` |
| `WEATHER_CORRELATION_ENABLED` | Weather correlation analysis | `True` |
| `ROUTE_LAUNDERING_DETECTION_ENABLED` | Route laundering detection | `True` |
| `ROUTE_LAUNDERING_SCORING_ENABLED` | Route laundering scoring contribution | `True` |
| `PI_CYCLING_DETECTION_ENABLED` | P&I club cycling detection | `True` |
| `PI_CYCLING_SCORING_ENABLED` | P&I cycling scoring contribution | `True` |
| `SPARSE_TRANSMISSION_DETECTION_ENABLED` | Sparse AIS transmission detection | `True` |
| `SPARSE_TRANSMISSION_SCORING_ENABLED` | Sparse transmission scoring contribution | `True` |
| `TYPE_CONSISTENCY_DETECTION_ENABLED` | Vessel type consistency checks | `True` |
| `TYPE_CONSISTENCY_SCORING_ENABLED` | Type consistency scoring contribution | `True` |
| `WATCHLIST_STUB_SCORING_ENABLED` | Watchlist stub scoring | `True` |

Additional detection threshold for route laundering:

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `ROUTE_LAUNDERING_LOOKBACK_DAYS` | `int` | `180` | Lookback window for route laundering analysis. |

## Historical Data Pipeline

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `HISTORY_BACKFILL_ENABLED` | `bool` | `False` | Master switch for historical backfill. |
| `NOAA_BACKFILL_ENABLED` | `bool` | `False` | Enable NOAA historical AIS backfill. |
| `DMA_BACKFILL_ENABLED` | `bool` | `False` | Enable DMA historical AIS backfill. |
| `GFW_GAPS_BACKFILL_ENABLED` | `bool` | `False` | Enable GFW AIS gap events backfill. |
| `GFW_ENCOUNTERS_BACKFILL_ENABLED` | `bool` | `False` | Enable GFW encounter events backfill. |
| `GFW_PORT_VISITS_BACKFILL_ENABLED` | `bool` | `False` | Enable GFW port visit events backfill. |
| `HISTORY_BACKFILL_INTERVAL_HOURS` | `int` | `168` | Backfill polling interval (default 1 week). |

## Email Notifications

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `RESEND_API_KEY` | `str \| None` | `None` | Resend API key for transactional email. |
| `EMAIL_FROM_DOMAIN` | `str` | `radiancefleet.com` | Sender domain for outbound email. |
| `SMTP_HOST` | `str \| None` | `None` | SMTP server hostname. |
| `SMTP_PORT` | `int` | `587` | SMTP server port. |
| `SMTP_USER` | `str \| None` | `None` | SMTP authentication username. |
| `SMTP_PASS` | `str \| None` | `None` | SMTP authentication password. |

## Sentry Error Tracking

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `SENTRY_DSN` | `str \| None` | `None` | Sentry DSN. Sentry is only initialized if this is set. |
| `SENTRY_TRACES_SAMPLE_RATE` | `float` | `0.1` | Fraction of transactions sent to Sentry (0.0-1.0). |
| `SENTRY_ENVIRONMENT` | `str` | `production` | Sentry environment tag. |

## Operations

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `PROMETHEUS_ENABLED` | `bool` | `False` | Enable Prometheus metrics endpoint (`/metrics`). |

---

## Additional Runtime Patterns

### SCORING_OVERRIDES

Risk scoring weights can be overridden at runtime via the `SCORING_OVERRIDES` environment variable. The value is a JSON object mapping signal names to weight multipliers:

```bash
SCORING_OVERRIDES='{"flag_hopping": 1.5, "dark_sts": 2.0, "draught_change": 0.5}'
```

Keys correspond to signal names defined in `config/risk_scoring.yaml`. Values are float multipliers applied to the base weight. Set a signal to `0.0` to effectively disable its scoring contribution without disabling detection.

### EXTRA_WHITELISTED_MMSIS

A comma-separated list of MMSI numbers to exclude from anomaly detection:

```bash
EXTRA_WHITELISTED_MMSIS="123456789,987654321,111222333"
```

Use this to suppress alerts for known-good vessels (e.g., coast guard, research vessels, or vessels already investigated and cleared).
