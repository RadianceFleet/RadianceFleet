"""RadianceFleet CLI — maritime anomaly detection for shadow fleet triage.

Commands:
  start              — first-time setup
  update             — daily data refresh + detection
  check-vessels      — vessel identity merge workflow (--list, --min-score, --cleanup)
  open               — launch web dashboard
  status             — system health check
  search             — vessel lookup by MMSI/IMO/name
  rescore            — re-run scoring + watchlist stub scoring
  evaluate-detector  — sample anomalies for holdout review
  confirm-detector   — re-enable scoring after drift review
  collect            — run periodic AIS data collection (--status to view history)
  history status     — coverage status for historical sources
  history gaps       — list uncovered date ranges
  history backfill   — backfill a specific source and date range
  history schedule   — run or preview the history scheduler
"""

from __future__ import annotations

import app.cli_archive as _cli_archive  # noqa: F401,E402
import app.cli_db as _cli_db  # noqa: F401,E402
import app.cli_history as _cli_history  # noqa: F401,E402
import app.cli_psc as _cli_psc  # noqa: F401,E402

# ---------------------------------------------------------------------------
# Import sub-command modules — registers commands on `app` / `history_app`
# ---------------------------------------------------------------------------
import app.cli_update as _cli_update  # noqa: F401,E402
import app.cli_vessels as _cli_vessels  # noqa: F401,E402
import app.cli_worker as _cli_worker  # noqa: F401,E402

# ---------------------------------------------------------------------------
# Core objects — shared by all sub-command modules
# ---------------------------------------------------------------------------
from app.cli_app import app, console, history_app  # noqa: F401

# ---------------------------------------------------------------------------
# Re-export helpers for backward compatibility (tests patch "app.cli._*")
# ---------------------------------------------------------------------------
from app.cli_helpers import *  # noqa: F401,F403
