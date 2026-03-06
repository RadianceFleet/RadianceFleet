# Changelog

All notable changes to RadianceFleet will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- OSINT-informed scoring: sanctioned port detection, temporal decay, KSE archetype matching, EEZ proximity signals

### Fixed
- pytest-timeout added and xdist disabled to prevent test suite freezing

### Removed
- **API**: Removed 33 unused endpoints with no frontend callers (11 individual detector triggers, 4 hunt write endpoints, 4 fleet/convoy/satellite duplicates, 12 vessel sub-resources, 1 admin alias, 1 merge-chain). CLI commands and underlying modules unaffected.

## [2.1.0] - 2026-03-01

### Added
- **Data Sources**: BarentsWatch AIS (Norwegian EEZ, OAuth 2.0), DMA historical AIS (Danish Straits, back to 2006), NOAA CMSP AIS downloader, AISHub aggregated feed client
- **Data Sources**: aisstream.io WebSocket consumer with configurable area filters and incremental ingestion
- **Detectors**: PSC detention loader (Tokyo/Black Sea/Abuja/Paris MOUs, EMSA ban API)
- **Detectors**: Route laundering, P&I cycling, sparse AIS transmission, vessel type consistency
- **Infrastructure**: Historical data pipeline with coverage tracking, time-ranged APIs, retention policies
- **Infrastructure**: Sample data generator rewrite: 55 vessels, 2,100+ AIS points
- **Identity**: GFW full identity extraction, callsign enrichment, VesselHistory scoring
- **Identity**: Vessel identity merging — detect, score, execute and reverse MMSI identity swaps
- **Identity**: Merge readiness diagnostics and `--diagnose` CLI flag
- **CLI**: Refactored from 57 commands to focused 6-command interface

### Fixed
- Scoring engine correctness: self-amplification, calibration, pipeline bugs
- Track naturalness, merge chain, dark coordination, destination detection accuracy
- Fingerprint thresholds lowered for sparse GFW data (300->20 pts, 24->2h)
- Feed outage reset, merge scoring, AISStream diagnostics
- IMO mismatch blocking, NULL timestamp guards, timezone-naive normalization
- DMA history tracking, OFAC test MMSI/IMO parsing

## [2.0.0] - 2026-03-01

### Added
- **Accuracy**: Sister ship anti-merge (overlapping AIS tracks block merge), negative merge signals (DWT/type/port mismatch)
- **Accuracy**: IMO fraud two-pass cross-check, forward-only provenance on gap events
- **Accuracy**: `feed_outage_detector.py` — 3x P95 baseline, adaptive per corridor, suppresses false gaps
- **Accuracy**: `confidence_classifier.py` — CONFIRMED/HIGH/MEDIUM/LOW/NONE anomaly classification
- **Accuracy**: `PipelineRun` model with drift monitoring and anomaly count tracking
- **Detectors**: P&I club validation (+25/+40/+15), fraudulent registry detection (+40/+20)
- **Detectors**: Stale AIS detection (+20), at-sea extended operations (+15/+25/+35)
- **Detectors**: ISM/P&I continuity across ownership changes (+20/+15), rename velocity (+15/+30/+25)
- **Detectors**: `destination_detector.py` — heading vs declared destination divergence (+40/+10/+20)
- **Detectors**: `sts_chain_detector.py` — multi-hop STS relay chain detection (+20/+40 per hop)
- **Detectors**: `scrapped_registry.py` — scrapped IMO reuse (+50), track replay detection (+45)
- **Merging**: `merge_chain.py` — BFS chain detection with min-link confidence
- **Merging**: `vessel_fingerprint.py` — 10-feature Mahalanobis distance (pure Python, no NumPy)
- **Merging**: `sar_correlator.py` — SAR-AIS correlation via drift ellipse, LOA, heading
- **Fleet Intelligence**: `ownership_graph.py` — shell chains, circular ownership, sanctions propagation
- **Fleet Intelligence**: `convoy_detector.py` — convoy, floating storage, Arctic no-ice-class detection
- **Fleet Intelligence**: `voyage_predictor.py` — route templates, Jaccard similarity, deviation scoring
- **Fleet Intelligence**: `cargo_inference.py` — draught-based laden/ballast + port context
- **Fleet Intelligence**: `weather_correlator.py` — NOAA GFS stub, speed deductions (-8/-15 kn)
- **Config**: `fraudulent_registries.yaml`, `legitimate_pi_clubs.yaml`, `scrapped_vessels.yaml`
- **CLI**: `rescore`, `evaluate-detector`, `confirm-detector` commands

## [1.6.0] - 2026-02-28

### Added
- **Auth**: JWT admin authentication (HS256, 30-min sessions, `require_admin` dependency)
- **Frontend**: `LoginModal.tsx` with JWT session storage
- **Frontend**: `TipForm.tsx` — public crowdsourced anomaly report submission
- **Frontend**: `SubscribeForm.tsx` — double opt-in email alert subscriptions
- **API**: `TipSubmission` model with moderation queue
- **API**: `AlertSubscription` model with double opt-in confirmation
- **Infrastructure**: `email_notifier.py` — Resend API (primary) + SMTP fallback
- **Enrichment**: `equasis_client.py` — vessel metadata scraping (opt-in, disabled by default)
- **Config**: `legitimate_operators.yaml` — P&I clubs, national carriers, ferry lines for false positive suppression

## [1.5.0] - 2026-02-28

### Added
- **Detectors**: `track_naturalness_detector.py` — Kalman filter residual analysis, 5 statistical features, confidence tiers (+25/+35/+45)
- **Detectors**: `draught_detector.py` + `DraughtChangeEvent` model — class-specific thresholds, corroborating-signal-only trigger
- **Detectors**: `stateless_detector.py` — 3-tier MMSI detection (unallocated/landlocked/micro-territory)
- **Detectors**: `flag_hopping_detector.py` — frequency-based scoring with ownership discount and registry modifiers
- **Detectors**: `imo_fraud_detector.py` — simultaneous IMO collision (+45), near-miss detection (+20)
- **Detectors**: Dark STS Phase C — rolling 7-day P95 gap threshold, tiered proximity scoring, zone-wide jamming suppression
- **Fleet**: `owner_dedup.py` — first-letter bucketing, rapidfuzz token_sort_ratio >= 85, union-find clustering
- **Fleet**: `fleet_analyzer.py` — 6 fleet patterns (STS concentration, dark coordination, flag diversity, risk avg, shared manager/P&I)
- **Models**: `OwnerCluster`, `OwnerClusterMember`, `FleetAlert`, `CorridorGapBaseline`, `SatelliteTaskingCandidate`
- **Infrastructure**: `itu_mid_table.py` — complete ITU MID allocation table (200+ entries)
- **Infrastructure**: 12 dual feature flags (detection + scoring per detector) for shadow-mode validation
- **Infrastructure**: Inspector-based `_run_migrations()` replacing broad try/except

## [1.4.0] - 2026-02-28

### Added
- **Data Quality**: Port resolver with rapidfuzz threshold 80 + Cyrillic normalization
- **Data Quality**: Satellite query last-known AIS fallback before North Sea default
- **Data Quality**: Pydantic schema validation replacing `body: dict` in 5 routes
- **Data Quality**: `_audit_log()` calls on ~15 state-changing routes
- **API**: Detection endpoints — POST `/detect/cross-receiver`, `/detect/handshake`, `/detect/mmsi-cloning`
- **API**: GET `/port-calls/{vessel_id}`, GET `/health/data-freshness`
- **API**: `last_ais_received_utc` column, `data_age_hours` + `data_freshness_warning` in vessel detail
- **API**: Coverage quality mapping — corridor-to-region keyword matching, YAML-driven quality tiers
- **API**: Alert enrichment — linked spoofing (+-1d), loitering (+-7d), STS events (+-7d) in alert detail
- **API**: Recurring pattern fields: `prior_similar_count`, `is_recurring_pattern` on gap events
- **Frontend**: `VesselTimeline.tsx` — color-coded vertical timeline
- **Frontend**: `VerificationPanel.tsx` + `VerificationBadge.tsx` — ownership verification UI
- **Frontend**: `Pagination.tsx` — reusable component added to 5 list pages
- **Frontend**: `CreateCorridorModal.tsx` + corridor delete with confirmation
- **Frontend**: "Recurring" pattern badge and "Patterns only" filter in AlertList
- **Tests**: 233 tests across 16 files covering detectors, endpoints, and alert enrichment

## [1.3.0] - 2026-02-28

### Added
- **Corridors**: Gulf of Oman, Bulgaria, Cyprus, Cape Verde, Khor al Zubair added to `corridors.yaml`
- **Flags**: BB (Barbados) + GN (Guinea) added to `RUSSIAN_ORIGIN_FLAGS`
- **Scoring**: Vessel age split — 10-15y (0 pts), 15-20y (+5 pts)
- **Scoring**: Repeat STS partnerships (+30), flag+corridor coupling (+20), invalid metadata (+10/+15), voyage cycle (+30)
- **Scoring**: Selective dark zone evasion (+20) — individual vessel dark vs zone-wide jamming
- **Data Sources**: `gfw_client.py` — GFW encounter events and port visit import
- **Data Sources**: `kystverket_client.py` — Norwegian AIS TCP stream (pyais NMEA decoder)
- **Data Sources**: `digitraffic_client.py` — Finnish AIS REST + port call API
- **Data Sources**: `crea_client.py` — CREA Russia Fossil Tracker with `CreaVoyage` model
- **Data Sources**: FleetLeaks + Ukraine GUR watchlist loaders
- **Detectors**: `cross_receiver_detector.py` — position disagreement > 5nm across sources
- **Detectors**: `handshake_detector.py` — identity swap at proximity (< 1nm)
- **Detectors**: `fake_position_detector.py` — kinematic impossibility (> 25kn implied speed)
- **Models**: `AISObservation` — raw per-source AIS storage with 72h rolling window
- **Verification**: `paid_verification.py` — Skylight, Spire, SeaWeb stubs with budget enforcement
- **Verification**: `VerificationLog` model, PATCH `/vessels/{id}/owner`, POST `/vessels/{id}/verify`
- **Config**: `bunkering_exclusions.yaml` — known bunkering MMSIs for STS false positive reduction
- **Infrastructure**: Cyrillic transliteration via `unidecode` for watchlist matching
- **Infrastructure**: `port_resolver.py` — geo-nearest + fuzzy name matching for port resolution

## [1.2.0] - 2026-02-28

### Added
- Geographic expansion: 18 ports, 15 corridors, composite index for performance

### Fixed
- AIS data integrity: sentinel filtering, MMSI validation, timestamp parsing across all ingestion paths
- Scoring accuracy: 6 calibration fixes to reduce false positives and score compression

## [1.1.0]

### Added
- GFW 4Wings API integration, MMSI flag derivation, vessel enrichment pipeline
- Frontend refactor + backend hardening
- Vessel hunt (FR9): `create_target_profile()`, `create_search_mission()`, `find_hunt_candidates()`
- Government alert package export (FR10): structured Markdown + JSON evidence cards
- Dark vessel detection pipeline: `dark_vessel_discovery.py` + `dark_vessel_hunt` CLI command

### Fixed
- Scoring architecture, detection logic, data integrity + missing signals

## [1.0.0]

### Added
- **FR1**: AIS gap detection — class B noise filter, velocity plausibility ratio, rotated ellipse movement envelopes
- **FR2**: Spoofing detection — 5 typologies (impossible speed, anchor-in-ocean, circle spoof, impossible reappearance, stationary MMSI)
- **FR3**: Loitering detection — SOG-based 1-hour windows, laid-up vessel flags (30/60 day), loiter-gap-loiter STS linking
- **FR4**: STS detection — Phase A proximity (200m, 8+ windows), Phase B approaching vector and heading filter
- **FR5**: Corridor correlation — trajectory-based matching, dark zone detection with adjusted scoring
- **FR6**: Risk scoring — 12 signal categories, configurable weights in `risk_scoring.yaml`, config hash tracking
- **FR7**: Satellite check preparation — movement envelope bounding box, Copernicus Open Access Hub query URL
- **FR8**: Evidence export — Markdown/JSON evidence cards, GFW dark vessel import and correlation
- 14 SQLAlchemy models, 20+ REST API endpoints (FastAPI), 18 CLI commands (Typer)
- Watchlist loaders: OFAC SDN, KSE Institute, OpenSanctions (rapidfuzz 85% match)
- Bulk CSV export with StreamingResponse
- React frontend scaffolding with alert list, filter panel, alert detail with map
- Sample data generator: 129 AIS points, 7 vessels covering all anomaly typologies
