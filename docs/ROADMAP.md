# Roadmap

---

## v1.0 — Complete

All core feature requirements (FR1–FR8) are implemented and tested.

| Feature | Description |
|---------|-------------|
| FR1 — AIS Gap Detection | Identifies vessels that disappear from AIS for anomalous durations. Class B noise filter, velocity plausibility ratio, rotated ellipse movement envelopes. |
| FR2 — Spoofing Detection | Five typologies: impossible speed, anchor-in-ocean, circle spoof, impossible reappearance, stationary MMSI broadcast. |
| FR3 — Loitering Detection | SOG-based loitering using 1-hour windows. Laid-up vessel flags (30-day, 60-day). Loiter-gap-loiter linking for STS zone context. |
| FR4 — STS Detection | Two-phase ship-to-ship transfer detection: Phase A proximity (200m, 8+ windows), Phase B approaching vector and heading filter. |
| FR5 — Corridor Correlation | Shapely bounding-box trajectory corridor matching. Dark zone detection with adjusted scoring. |
| FR6 — Risk Scoring | 12 signal categories with configurable weights in `config/risk_scoring.yaml`. Scoring date parameter for reproducibility (NFR3). Config hash tracked per rescore run. |
| FR7 — Satellite Check Preparation | Computes movement envelope bounding box. Generates Copernicus Open Access Hub query URL. Data source coverage metadata. |
| FR8 — Evidence Export | Markdown and JSON evidence cards. Blocked on `new` status as analyst review gate (NFR7). GFW dark vessel import and correlation. |

### v1.0 Scope Summary

- 14 SQLAlchemy models + WKT geometry via Shapely
- 20+ REST API endpoints (FastAPI)
- 18 CLI commands (Typer)
- Watchlist loaders for OFAC SDN, KSE Institute, OpenSanctions (rapidfuzz 85% match threshold)
- Bulk CSV export with StreamingResponse (no memory buffering)
- Sample data generator: 129 AIS points, 7 vessels covering all anomaly typologies

---

## v1.1 — Complete

Originally planned as the next milestone; all items are now implemented.

| Feature | Description |
|---------|-------------|
| FR9 — Named Vessel Hunt | `vessel_hunt.py`: `create_target_profile()`, `create_search_mission()`, `find_hunt_candidates()`, `finalize_mission()`. Drift ellipse construction with scored dark vessel detection candidates. |
| FR10 — Government Alert Package | `evidence_export.py`: structured Markdown + JSON evidence cards with chain-of-custody metadata. 462 lines of export logic. |
| DarkVesselDetection Integration | `dark_vessel_discovery.py` + `dark_vessel_hunt` CLI command. Dark vessel detections integrated as scored signals in risk pipeline. |

---

## v1.2 — Complete (Phases A–D)

Blind spot closures, new data sources, and three new spoofing detector families.

| Feature | Description |
|---------|-------------|
| New STS corridors | Gulf of Oman, Bulgaria, Cyprus, Cape Verde, Khor al Zubair added to `corridors.yaml`. |
| Expanded flag registry | BB (Barbados) + GN (Guinea) added to `RUSSIAN_ORIGIN_FLAGS`. Vessel age 10–15y (0 pts), 15–20y (+5 pts) split. |
| Bunkering exclusions | `config/bunkering_exclusions.yaml` — known bunkering MMSIs reduce STS false positives. |
| Cyrillic transliteration | `unidecode` normalization for watchlist name matching. |
| 4 new scoring signals | Repeat STS partnerships (+30), flag+corridor coupling (+20), invalid metadata (+10/+15), voyage cycle (+30). Selective dark zone evasion (+20). |
| GFW data expansion | `gfw_client.py`: encounter events and port visit import. `port_resolver.py`: geo-nearest + fuzzy name matching. |
| Nordic AIS sources | `kystverket_client.py`: Norwegian TCP stream (pyais NMEA decoder). `digitraffic_client.py`: Finnish AIS REST + port call API. |
| CREA Fossil Tracker | `crea_client.py`: Russia fossil fuel export tracking. `CreaVoyage` model with dedup. |
| Watchlist expansion | FleetLeaks + Ukraine GUR watchlist loaders. |
| Phase C spoofing detectors | `cross_receiver_detector.py` (position disagreement >5nm), `handshake_detector.py` (identity swap at proximity), `fake_position_detector.py` (kinematic impossibility >25kn). `AISObservation` model for raw per-source AIS storage. |
| Paid verification stubs | `paid_verification.py`: Skylight, Spire, SeaWeb provider stubs with budget enforcement. `VerificationLog` model. |

---

## v1.3 — Complete (Phases G–J)

Data quality hardening, detection API exposure, frontend investigation UX.

| Feature | Description |
|---------|-------------|
| Data quality | `port_resolver.py`: rapidfuzz threshold 80 + Cyrillic normalization. `satellite_query.py`: last-known AIS fallback before North Sea default. Pydantic schema validation in 5 routes. `_audit_log()` on ~15 state-changing routes. |
| Detection endpoints | POST `/detect/cross-receiver`, `/detect/handshake`, `/detect/mmsi-cloning`, GET `/port-calls/{vessel_id}`. |
| Data freshness API | GET `/health/data-freshness`: staleness_minutes, vessels updated last 1h/24h. `last_ais_received_utc` column on vessels. `data_age_hours` and `data_freshness_warning` in vessel detail. |
| Coverage quality mapping | Corridor→region keyword mapping, YAML-driven (GOOD/MODERATE/PARTIAL/POOR/NONE/UNKNOWN). |
| Alert enrichment | Linked spoofing anomalies (±1d), loitering (±7d), STS events (±7d) in alert detail. `prior_similar_count`, `is_recurring_pattern` on gap events. |
| Frontend — investigation UX | `VesselTimeline.tsx`: color-coded timeline. `VerificationPanel.tsx` + `VerificationBadge.tsx`: ownership verification UI. `Pagination.tsx` on 5 list pages. `CreateCorridorModal.tsx`. Corridor delete with confirmation. "Recurring" badge + filter in AlertList. |
| Test coverage | 233 tests across 16 files covering all new endpoints and detectors. |

---

## v1.4 — Complete (Phases K–O)

Advanced statistical detectors, identity fraud detection, dark STS phase C, fleet intelligence.

| Feature | Description |
|---------|-------------|
| Track naturalness detector | `track_naturalness_detector.py`: Kalman filter residual analysis. 5 statistical features (residual std, speed autocorrelation, heading entropy, course kurtosis). Confidence tiers: 5/5→+45, 4/5→+35, 3/5→+25. |
| Draught intelligence | `draught_detector.py` + `DraughtChangeEvent` model. Class-specific thresholds (VLCC 3.0m, Suezmax 2.0m, Aframax 1.5m, Panamax 1.0m). Corroborating-signal-only trigger. 25nm offshore terminal exclusion. |
| Stateless MMSI detector | `stateless_detector.py`: unallocated (+35), landlocked MID (+20), micro-territory (+10). Full ITU MID table in `itu_mid_table.py` (200+ allocations). |
| Flag hopping detector | `flag_hopping_detector.py`: 2/90d +20, 3+/90d +40, 5+/365d +50. Ownership discount and registry modifiers. |
| IMO fraud detector | `imo_fraud_detector.py`: simultaneous IMO collision (+45, checksum + moving + >500nm apart), near-miss (+20, ≥2 qualifiers). |
| Dark STS Phase C | `gap_rate_baseline.py`: rolling 7-day P95 gap threshold per corridor. `_phase_c_dark_dark()` in `sts_detector.py`. Tiered proximity: <5nm HIGH +30, 5-15nm MEDIUM +20, 15-50nm LOW +10. P95 suppression for zone-wide jamming. |
| Fleet/owner intelligence | `owner_dedup.py`: first-letter bucketing + rapidfuzz token_sort_ratio ≥85, union-find clustering. `fleet_analyzer.py`: 6 fleet patterns (STS concentration, dark coordination, flag diversity, risk avg, shared manager, shared P&I). `OwnerCluster`, `OwnerClusterMember`, `FleetAlert` models. |
| Shadow mode flags | 12 dual feature flags (detection + scoring per detector) for shadow-mode validation. |

---

## v1.5 — Complete (98% Dark Fleet Detection)

Accuracy foundation, 6 quick-win detectors, 3 major gap closures, signal merging overhaul, fleet analytics.

| Feature | Description |
|---------|-------------|
| Merge accuracy fixes | Sister ship anti-merge (overlapping AIS blocks). Negative merge signals (DWT mismatch −15, type mismatch −10, conflicting ports −15). IMO fraud two-pass cross-check. Forward-only provenance (`original_vessel_id` on gap events). |
| Feed outage detection | `feed_outage_detector.py`: 3× P95 baseline, adaptive per corridor. Suppresses false gaps during receiver outages. |
| Confidence classifier | `confidence_classifier.py`: CONFIRMED/HIGH/MEDIUM/LOW/NONE classification for all anomalies. `PipelineRun` model with drift monitoring. |
| Quick-win detectors | P&I validation (+25/+40/+15), fraudulent registries (+40/+20), stale AIS (+20), at-sea extended ops (+15/+25/+35), ISM/P&I continuity across ownership (+20/+15), rename velocity (+15/+30/+25). `config/fraudulent_registries.yaml`, `config/legitimate_pi_clubs.yaml`. |
| Destination mismatch detector | `destination_detector.py`: heading vs declared destination divergence scoring (+40/+10/+20). |
| STS relay chain detector | `sts_chain_detector.py`: multi-hop STS relay detection (+20/+40 per hop). |
| Scrapped vessel registry | `scrapped_registry.py`: scrapped IMO reuse (+50), track replay (+45). `config/scrapped_vessels.yaml`. |
| Vessel fingerprinting | `vessel_fingerprint.py`: 10-feature Mahalanobis distance behavioral fingerprint (718 lines, pure Python, no NumPy). |
| SAR-AIS correlator | `sar_correlator.py`: drift ellipse + LOA + heading correlation for SAR image matching. `SatelliteTaskingCandidate` model. |
| Corporate ownership graph | `ownership_graph.py`: shell chains, circular ownership detection, sanctions propagation across clusters. |
| Convoy / storage detector | `convoy_detector.py`: convoy + floating storage + Arctic no-ice-class detection. |
| Voyage prediction | `voyage_predictor.py`: route templates, Jaccard similarity, deviation scoring. `RouteTemplate` model. |
| Cargo inference | `cargo_inference.py`: draught-based laden/ballast state + port context classification. |
| Weather correlator | `weather_correlator.py`: Open-Meteo Archive API (free, no key) — wind speed correlation with LRU cache. Scoring deductions (-8/-15 kn) for weather-explained speed anomalies. |

---

## v1.6 — Complete (Public Platform)

Multi-user deployment, crowdsourced tips, alert subscriptions, admin authentication.

| Feature | Description |
|---------|-------------|
| JWT admin auth | `auth.py`: HS256 JWT, 30-minute sessions, `require_admin` FastAPI dependency. Replaces API key for write-only protection on public instance. |
| Tip submission | `TipSubmission` model + `TipForm.tsx`: public crowdsourced anomaly reports with moderation queue. |
| Alert subscriptions | `AlertSubscription` model + `SubscribeForm.tsx`: double opt-in email notifications for new alerts. |
| Email notifier | `email_notifier.py`: Resend API (primary) + SMTP fallback. Confirmation links with `PUBLIC_URL`. |
| Frontend: auth UI | `LoginModal.tsx` + JWT session storage. |
| Legitimate operators | `config/legitimate_operators.yaml`: P&I clubs, national carriers, ferry lines — suppresses false positives for known-benign operators. |
| Equasis enrichment | `equasis_client.py`: vessel metadata scraping (opt-in, disabled by default — ToS restricted). DWT, vessel type, year_built, callsign. `is_heuristic_dwt` provenance flag. |

---

## v2 — Complete (Data Sources Expansion)

All v1 "ideas" that required external integrations are now implemented.

| Feature | Description |
|---------|-------------|
| Real-time AIS streaming | `aisstream_client.py` (669 lines): aisstream.io WebSocket consumer. Configurable area filters via corridor bounding boxes. Incremental AIS ingestion with dedup. |
| ML vessel re-identification | `vessel_fingerprint.py`: Mahalanobis distance behavioral fingerprinting for MMSI/name-change tracking. 10-feature vector from gap, loitering, and STS history. |
| SAR satellite correlation | `sar_correlator.py`: SAR-AIS correlation using drift ellipses, LOA matching, heading alignment. `SatelliteTaskingCandidate` output model. |
| Beneficial ownership tracing | `ownership_graph.py` + `owner_dedup.py` + `paid_verification.py`: shell chain detection, rapidfuzz owner clustering, Skylight/Spire/SeaWeb paid API stubs. Equasis + OpenCorporates deep-links in vessel detail. |
| Port state control | `psc_loader.py` (216 lines): OpenSanctions FTM JSON (Tokyo/Black Sea/Abuja MOUs) + EMSA ban API (Paris MOU). IMO-primary, fuzzy name fallback. `psc_detained_last_12m` on Vessel. |
| BarentsWatch AIS | `barentswatch_client.py`: Norwegian EEZ + Svalbard AIS (OAuth 2.0 Client Credentials). Covers Murmansk corridor. |
| Danish Maritime Authority AIS | `dma_client.py`: historical AIS CSV archives from web.ais.dk/aisdata/ (daily files back to 2006). Every Russian shadow tanker transits Danish Straits. |
| NOAA historical AIS | `noaa_client.py`: NOAA CMSP AIS data downloader and batch importer. |
| AISHub integration | `aishub_client.py`: AISHub aggregated feed client. |

---

## v2.1 — Complete (OSINT Scoring)

| Feature | Description |
|---------|-------------|
| Sanctioned port detection | Scoring signal for vessels calling at known sanctioned ports. |
| Temporal decay | Risk signal decay over time for aging anomalies. |
| KSE archetype matching | Pattern matching against KSE Institute shadow fleet archetypes. |
| EEZ proximity signals | Scoring for vessels operating near sensitive exclusive economic zones. |

---

## v3.0 — Complete (Infrastructure & API Consolidation)

Production infrastructure, frontend expansion, data quality hardening, and API surface cleanup.

| Feature | Description |
|---------|-------------|
| Docker & CI | Multi-stage Dockerfile (uv+node), docker-compose.yml (web+postgres+cron). GitHub Actions CI (uv+ruff+pytest + npm+tsc+build). |
| Structured logging | `structlog` integration (JSON in prod, console in dev). `LOG_FORMAT` setting. |
| Sentry integration | Optional `sentry-sdk[fastapi]` dependency, init only if `SENTRY_DSN` set. |
| Rate limiting | slowapi tiered rate limiting: viewer 30/min, admin 120/min, default 60/min per IP. |
| Circuit breakers | `pybreaker` on 6 external API clients. Circuit state exposed in `/health`. |
| SPA serving | `StaticFiles` + catch-all route serves frontend build from backend. |
| Merge chain detection | `merge_chain.py`: BFS chain detection wrapper, min-link confidence scoring, `GET /merge-chains` endpoint. |
| AIS cargo type parsing | Ship type code → cargo type mapping with mismatch scoring. |
| Timestamp priority | AIS source timestamp priority prevents stale satellite data from overwriting newer positions. |
| Ingestion persistence | `IngestionStatus` model tracks ingestion state in database instead of `app.state`. |
| Data completeness cap | Prevents under-tracked vessels from scoring CRITICAL (exempt high-confidence). |
| Med STS FP reduction | Anchorage exclusion zones in `config/` reduce Mediterranean STS false positives. |
| Watchlist auto-update | Scheduler with per-source intervals for automatic watchlist refreshes. |
| Frontend pages | Fleet Analysis, Ownership Graph, Detector Results, Voyage Prediction pages. Batch alert operations (Mark Reviewed, Export Selected CSV). |
| Frontend tests | Vitest setup with 23 smoke + interaction tests. |
| Silent failure audit | Logging added to all bare `except` blocks in risk_scoring, routes_admin, routes_detection. |
| Routes refactor | `routes.py` (3,220 lines) split into 5 sub-routers. |
| API consolidation | Removed 33 unused endpoints with no frontend callers (11 detector triggers, 4 hunt writes, 4 fleet/convoy/satellite duplicates, 12 vessel sub-resources, 1 admin alias, 1 merge-chain). ~1,313 lines deleted. Endpoints reduced from ~114 to ~76. CLI and modules unaffected. |

---

## v3.1 — Complete (Accuracy Validation & Frontend UI Gaps)

Phase 1 ("Usable by a journalist today") from the strategic product analysis — accuracy foundation and frontend surfaces for existing backend capabilities.

| Feature | Description |
|---------|-------------|
| Accuracy validation harness | `validation_harness.py`: confusion matrix, precision, recall, F2 score, PR-AUC. `sweep_thresholds()` for precision-recall curves (0–200). `analyst_feedback_metrics()` for FP rate aggregation. `signal_effectiveness_report()` with lift analysis. `detector_correlation_report()` for signal pair FP rates. |
| Ground truth loader | `ground_truth_loader.py`: CSV import for KSE shadow fleet, OFAC SDN, and clean baseline vessels. |
| Accuracy dashboard | `AccuracyDashboardPage.tsx`: validation metrics with `PRCurveChart.tsx` (precision-recall scatter) and `FPRateByBandChart.tsx` (TP/FP by risk band). |
| Hunt UI page | `HuntPage.tsx` + `useHunt.ts`: frontend surface for the vessel hunt workflow (targets, missions, candidate scoring). Backend existed since v1.1 — now accessible without CLI. |
| Charting library | `recharts` v3.8.0 integrated. `CorridorActivityChart.tsx`, `ScoreDistributionChart.tsx` added alongside accuracy charts. |
| Tips admin page | `TipsAdminPage.tsx` + `useTips.ts`: tip moderation UI with PENDING/REVIEWED/ACTIONED/DISMISSED statuses and analyst notes. |
| Merge candidates page | `MergeCandidatesPage.tsx`: merge candidate table with confirm/reject actions. |
| Bulk evidence export | `AlertExportPanel.tsx`: per-alert Markdown/JSON export. `ExportButton.tsx`: bulk CSV export. Backend `/alerts/export` and `/alerts/{id}/export` endpoints. |

---

## v3.2 — Complete (Phase 1 Leftovers + Community Growth)

| Feature | Description |
|---------|-------------|
| Docker Hub CI | Docker publish job in GitHub Actions — pushes to Docker Hub + ghcr.io on `main` push. Tags `latest` + git SHA. GHA build cache. |
| Merge chain graph | `GET /merge-chains` endpoint with hydrated nodes/edges. SVG graph component with confidence coloring, vessel navigation. Table/Graph toggle in MergeCandidatesPage. |
| Track export | `GET /vessels/{id}/track.geojson` (RFC 7946) and `GET /vessels/{id}/track.kml` (gx:Track, XML-safe names). |
| PDF evidence report | `POST /alerts/{id}/export?format=pdf` via fpdf2. DejaVu Unicode font in Docker, Helvetica fallback. Frontend blob download bypass. |
| Coverage map overlay | `GET /coverage/geojson` with WKT polygon geometry per region. Frontend overlay with quality-based color coding. |
| Methodology document | `docs/METHODOLOGY.md` — 7-section document covering purpose, data sources, detection, scoring, validation, limitations, interpretation. |
| API documentation polish | OpenAPI tag descriptions, deprecation policy, docs path fix, new endpoint docs in API.md. |

---

## v3.3 — Complete (Multi-Analyst + Satellite Orders + PSC Enhancement)

| Feature | Description |
|---------|-------------|
| Multi-analyst workflow | `Analyst` model with roles (analyst, senior_analyst, admin). Per-analyst JWT auth, DB-based login, alert assignment, edit locks (5-min TTL), optimistic locking (version field), evidence chain-of-custody (exported_by, approval workflow). Legacy single-password login preserved. |
| Commercial satellite order placement | `SatelliteOrder` + `SatelliteOrderLog` models. Provider abstraction with Planet Labs and Capella Space clients (httpx, circuit breaker). 8 API endpoints for order lifecycle (search, submit, poll, cancel). Budget enforcement. CLI sub-commands. |
| PSC detention history | `PscDetention` model replacing boolean-only flags. Full detention records with MOU source, deficiency counts, port/country, authority. 6 new scoring signals (multiplicity, recency, ban type). Loader refactored for upsert + summary sync. CLI sub-commands. |

---

## Open / In Progress

| Item | Status |
|------|--------|
| Live demo instance | **Live** at `https://www.radiancefleet.com` on Railway (Hobby plan). PostgreSQL backend, 11,903 alerts across 5,498 vessels, all 11 circuit breakers healthy. Data loaded: AIS positions, OFAC/KSE/OpenSanctions watchlists, gap/spoofing/loitering detection complete, risk scoring applied. |
| Additional PSC MOUs | `psc_loader.py` covers Tokyo, Black Sea, Abuja, Paris MOUs. Remaining MOUs researched (2026-03): **Mediterranean** (THETIS-Med, bulk download forbidden), **Indian Ocean** (web search form only at iomou.org, no bulk/API), **Riyadh** (PDF reports only), **Viña del Mar** (PDF reports only). None offer programmatic data access. Will integrate if any publish structured data. |

---

## Known Limitations

- **SQLite geometry**: Corridor correlation uses Shapely bounding-box checks on WKT text columns (works on both SQLite and PostgreSQL). For complex polygons or large corridor sets, migrating to native PostGIS ST_Intersects with GiST indexes would improve accuracy and performance.
- **Satellite order providers**: All four providers (Planet Labs, Capella Space, Maxar, Umbra) are fully implemented but require valid API keys. Orders stay in draft until analyst confirms (budget safety).
- **Equasis ToS**: `equasis_client.py` is disabled by default (`EQUASIS_SCRAPING_ENABLED=false`). Automated Equasis access violates their Terms of Service. Use Datalastic API for production enrichment.
- **BarentsWatch 14-day limit**: Historical data older than 14 days is purged by BarentsWatch; only recent Norwegian EEZ data is available.
