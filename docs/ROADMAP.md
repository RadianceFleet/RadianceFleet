# Roadmap

---

## v1.0 — Complete

All core feature requirements (FR1–FR8) are implemented and tested (85/85 tests passing).

| Feature | Description |
|---------|-------------|
| FR1 — AIS Gap Detection | Identifies vessels that disappear from AIS for anomalous durations. Class B noise filter, velocity plausibility ratio, rotated ellipse movement envelopes. |
| FR2 — Spoofing Detection | Five typologies: impossible speed, anchor-in-ocean, circle spoof, impossible reappearance, stationary MMSI broadcast. |
| FR3 — Loitering Detection | SOG-based loitering using 1-hour windows. Laid-up vessel flags (30-day, 60-day). Loiter-gap-loiter linking for STS zone context. |
| FR4 — STS Detection | Two-phase ship-to-ship transfer detection: Phase A proximity (200m, 8+ windows), Phase B approaching vector and heading filter. |
| FR5 — Corridor Correlation | ST_Intersects trajectory-based corridor matching. Dark zone detection with adjusted scoring. |
| FR6 — Risk Scoring | 12 signal categories with configurable weights in `config/risk_scoring.yaml`. Scoring date parameter for reproducibility (NFR3). Config hash tracked per rescore run. |
| FR7 — Satellite Check Preparation | Computes movement envelope bounding box. Generates Copernicus Open Access Hub query URL. Data source coverage metadata. |
| FR8 — Evidence Export | Markdown and JSON evidence cards. Blocked on `new` status as analyst review gate (NFR7). GFW dark vessel import and correlation. |

### v1.0 Scope Summary

- 14 SQLAlchemy models + PostGIS spatial types
- 20+ REST API endpoints (FastAPI)
- 18 CLI commands (Typer)
- Watchlist loaders for OFAC SDN, KSE Institute, OpenSanctions (rapidfuzz 85% match threshold)
- Bulk CSV export with StreamingResponse (no memory buffering)
- Sample data generator: 129 AIS points, 7 vessels covering all anomaly typologies

---

## v1.1 — Planned

| Feature | Description |
|---------|-------------|
| FR9 — Named Vessel Hunt | `SearchMission` and `HuntCandidate` models are already stubbed in the schema. Full implementation: analyst creates a named hunt for a vessel or vessel class, system monitors incoming AIS for matches, generates `HuntCandidate` hits with confidence scores. |
| FR10 — Government Alert Package | Structured export format for formal reporting to maritime authorities, coast guards, or sanctions enforcement bodies. Templated PDF or signed JSON bundle with chain-of-custody metadata. |
| DarkVesselDetection Integration | `DarkVesselDetection` model is already stubbed. Full implementation: integrate GFW dark vessel detections into the risk scoring pipeline as a scored signal rather than a reference-only annotation. |

### Known limitations addressed in v1.1

- No automated AIS streaming ingestion (aisstream.io integration)
- No multi-analyst workflow (single-user only in v1.0)
- No cargo type identification from AIS data

---

## v2 — Ideas

These are not committed features. They represent directions that may be explored depending on community feedback and contributor availability.

| Idea | Description |
|------|-------------|
| Real-time AIS streaming | Native aisstream.io WebSocket consumer with configurable vessel and area filters. Incremental gap detection on live feed. |
| Multi-user auth and audit log | JWT-based authentication, per-analyst alert ownership, and an immutable audit log of all status changes and exports. Required for multi-analyst newsroom or NGO deployments. |
| ML-based vessel re-identification | Behavioral fingerprinting to track vessels that change MMSI or name. Training on historical gap, loitering, and STS patterns. |
| Commercial satellite tasking API | Integration with Planet Labs or Maxar API to automatically task satellite imagery over a gap's movement envelope rather than generating a manual Copernicus URL. |
| Beneficial ownership tracing | Link vessel operator/owner to corporate registry data (OpenCorporates, national ship registries) to surface shell company structures. |
| Port state control integration | Ingest PSC detention and deficiency records to enrich the legitimacy scoring signals. |

---

## Known Limitations (v1.0)

- **No real-time ingestion**: AIS data must be imported as batch CSV files. Live streaming is not supported.
- **No cargo type identification**: AIS voyage data (cargo type declarations) is not parsed or scored. Vessel type from vessel registry is used instead.
- **No beneficial ownership tracing**: Vessel ownership and operator chains are not resolved. Watchlist matching is limited to vessel identity (MMSI, IMO, name).
- **Single-user only**: No access control, no per-analyst alert assignment, no concurrent session management. Not suitable for team use without a reverse proxy providing basic auth.
- **No multi-analyst workflow**: Evidence cards do not track reviewer chain of custody. All exports are attributed to the system, not to individual analysts.
- **SQLite limitation**: The SpatiaLite backend (used for local development without Docker) does not support all PostGIS spatial functions. ST_Intersects corridor correlation requires the PostgreSQL + PostGIS backend for production use.
- **Copernicus URL only**: Satellite check preparation generates a Copernicus query URL for manual download. It does not automate satellite order placement or image retrieval.
