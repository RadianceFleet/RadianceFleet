---
name: Bug report
about: Report a reproducible bug in RadianceFleet
labels: bug
---

## Description

<!-- A clear and concise description of what the bug is. -->

## Steps to Reproduce

1.
2.
3.

## Expected Behavior

<!-- What you expected to happen. -->

## Actual Behavior

<!-- What actually happened. Include the full error message and traceback if one was produced. -->

## AIS Data Sample

<!-- If the bug is triggered by specific AIS data, include a minimal reproducing sample.
     Do not attach full raw AIS files — a few rows is enough. -->

```
MMSI, Timestamp (UTC), Latitude, Longitude, SOG, COG
<!-- paste rows here -->
```

MMSI range: <!-- e.g. 273338710 -->
Timestamp range: <!-- e.g. 2024-01-15 06:00 UTC to 2024-01-15 14:00 UTC -->

## Environment

- OS: <!-- e.g. Ubuntu 22.04, macOS 14 -->
- Python version: <!-- e.g. 3.12.3 — run `python --version` -->
- RadianceFleet version / commit: <!-- e.g. v1.0.0 or git SHA -->
- Database backend: <!-- PostgreSQL + PostGIS (Docker) or SQLite + SpatiaLite -->
- Docker version (if using Docker): <!-- e.g. 24.0.7 -->

## Logs

<!-- Paste the relevant log output. For CLI commands, re-run with `--log-level debug` if available.
     For the API server, include the uvicorn stderr output. -->

```
<!-- paste logs here -->
```

## Additional Context

<!-- Anything else that might help: was this working before a specific commit, does it only happen with certain vessel types, etc. -->
