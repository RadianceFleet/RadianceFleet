"""Tests for RadianceFleet CLI commands."""
from __future__ import annotations

import inspect
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from app.cli import app


runner = CliRunner()


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._load_sample_data")
@patch("app.cli._import_corridors")
@patch("app.cli._is_first_run", return_value=True)
@patch("app.database.SessionLocal")
@patch("app.database.init_db")
def test_start_demo(mock_init, mock_sl, mock_first_run, mock_corridors, mock_sample,
                    mock_discover, mock_summary, mock_next):
    """start --demo calls init_db, _load_sample_data, and discover_dark_vessels."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    # Port count = 0 triggers seed_ports
    mock_db.query.return_value.count.return_value = 0
    with patch("scripts.seed_ports.seed_ports", return_value={"inserted": 100}):
        result = runner.invoke(app, ["start", "--demo"])

    assert result.exit_code == 0
    mock_init.assert_called_once()
    mock_sample.assert_called_once_with(mock_db)
    mock_discover.assert_called_once()


@patch("app.cli._is_first_run", return_value=False)
def test_start_already_configured(mock_first_run):
    """start when already configured suggests update instead."""
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 0
    assert "already set up" in result.output
    assert "update" in result.output


@patch("app.cli._is_first_run", return_value=True)
@patch("app.database.init_db")
def test_start_setup_failure(mock_init, mock_first_run):
    """start handles init_db failure with friendly error."""
    mock_init.side_effect = Exception("database locked")
    result = runner.invoke(app, ["start"])
    assert result.exit_code == 1
    assert "failed" in result.output.lower()


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._enrich_vessels")
@patch("app.cli._update_stream_ais")
@patch("app.cli._update_fetch_watchlists")
@patch("app.cli._import_corridors")
@patch("app.cli._is_first_run", return_value=True)
@patch("app.database.SessionLocal")
@patch("app.database.init_db")
def test_start_enrichment_called(mock_init, mock_sl, mock_first_run, mock_corridors,
                                 mock_fetch, mock_stream, mock_enrich, mock_discover,
                                 mock_summary, mock_next):
    """start (non-demo) calls _enrich_vessels."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_db.query.return_value.count.return_value = 1  # ports already seeded

    with patch("app.config.settings") as mock_settings:
        mock_settings.AISSTREAM_API_KEY = "test-key"
        result = runner.invoke(app, ["start"])

    assert result.exit_code == 0
    mock_enrich.assert_called_once_with(mock_db)
    mock_fetch.assert_called_once()
    mock_discover.assert_called_once()


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._update_stream_ais")
@patch("app.cli._update_fetch_watchlists")
@patch("app.database.SessionLocal")
def test_update_full_pipeline(mock_sl, mock_fetch, mock_stream, mock_discover, mock_summary, mock_next):
    """update calls all three phases."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    with patch("app.config.settings") as mock_settings:
        mock_settings.AISSTREAM_API_KEY = "test-key"
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    mock_fetch.assert_called_once_with(mock_db)
    mock_stream.assert_called_once()
    mock_discover.assert_called_once()


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._update_stream_ais")
@patch("app.cli._update_fetch_watchlists")
@patch("app.database.SessionLocal")
def test_update_offline(mock_sl, mock_fetch, mock_stream, mock_discover, mock_summary, mock_next):
    """update --offline skips fetch and stream, still runs detection."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    result = runner.invoke(app, ["update", "--offline"])

    assert result.exit_code == 0
    mock_fetch.assert_not_called()
    mock_stream.assert_not_called()
    mock_discover.assert_called_once()


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._update_stream_ais")
@patch("app.cli._update_fetch_watchlists")
@patch("app.database.SessionLocal")
def test_update_missing_api_key(mock_sl, mock_fetch, mock_stream, mock_discover, mock_summary, mock_next):
    """update without AISSTREAM_API_KEY skips streaming."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    with patch("app.config.settings") as mock_settings:
        mock_settings.AISSTREAM_API_KEY = ""
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    mock_fetch.assert_called_once()
    mock_stream.assert_not_called()
    assert "skipping" in result.output.lower() or "AISSTREAM" in result.output


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._update_stream_ais")
@patch("app.cli._update_fetch_watchlists")
@patch("app.database.SessionLocal")
def test_update_fetch_failure(mock_sl, mock_fetch, mock_stream, mock_discover, mock_summary, mock_next):
    """update continues to detection even if fetch fails."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_fetch.side_effect = RuntimeError("network error")

    with patch("app.config.settings") as mock_settings:
        mock_settings.AISSTREAM_API_KEY = "test-key"
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    assert "issues" in result.output.lower() or "continuing" in result.output.lower()
    mock_discover.assert_called_once()


# ---------------------------------------------------------------------------
# check-vessels
# ---------------------------------------------------------------------------


@patch("app.modules.identity_resolver.detect_merge_candidates")
@patch("app.database.SessionLocal")
def test_check_vessels_list(mock_sl, mock_detect):
    """check-vessels --list shows table of pending candidates."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_detect.return_value = {"auto_merged": 0, "candidates_created": 2, "skipped": 0}

    # Create mock candidates
    mc1 = MagicMock()
    mc1.candidate_id = 1
    mc1.vessel_a_id = 10
    mc1.vessel_b_id = 20
    mc1.time_delta_hours = 12.5
    mc1.distance_nm = 45.0
    mc1.confidence_score = 78

    mc2 = MagicMock()
    mc2.candidate_id = 2
    mc2.vessel_a_id = 30
    mc2.vessel_b_id = 40
    mc2.time_delta_hours = 8.0
    mc2.distance_nm = 22.0
    mc2.confidence_score = 65

    # Mock the query chain for both MergeCandidate and Vessel lookups
    mock_query = MagicMock()
    mock_query.filter.return_value.order_by.return_value.all.return_value = [mc1, mc2]
    mock_db.query.return_value = mock_query

    va = MagicMock()
    va.mmsi = "211379500"
    va.name = "TANKER ONE"
    mock_db.query.return_value.get.return_value = va

    result = runner.invoke(app, ["check-vessels", "--list"])
    assert result.exit_code == 0
    assert "Pending Merge Candidates" in result.output


@patch("app.modules.identity_resolver.detect_merge_candidates")
@patch("app.database.SessionLocal")
def test_check_vessels_auto(mock_sl, mock_detect):
    """check-vessels --auto reports auto-merge results."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_detect.return_value = {"auto_merged": 3, "candidates_created": 1, "skipped": 5}

    result = runner.invoke(app, ["check-vessels", "--auto"])
    assert result.exit_code == 0
    assert "Auto-merged: 3" in result.output


@patch("sys.stdin")
@patch("app.modules.identity_resolver.detect_merge_candidates")
@patch("app.database.SessionLocal")
def test_check_vessels_non_tty(mock_sl, mock_detect, mock_stdin):
    """check-vessels falls back to list mode when not a TTY."""
    mock_stdin.isatty.return_value = False
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_detect.return_value = {"auto_merged": 0, "candidates_created": 0, "skipped": 0}

    mock_query = MagicMock()
    mock_query.filter.return_value.order_by.return_value.all.return_value = []
    mock_db.query.return_value = mock_query

    result = runner.invoke(app, ["check-vessels"])
    assert result.exit_code == 0


@patch("typer.prompt", return_value="m")
@patch("app.modules.identity_resolver.execute_merge")
@patch("app.modules.identity_resolver.detect_merge_candidates")
@patch("app.database.SessionLocal")
def test_check_vessels_interactive_merge(mock_sl, mock_detect, mock_merge, mock_prompt):
    """Interactive merge updates candidate status on success."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_detect.return_value = {"auto_merged": 0, "candidates_created": 1, "skipped": 0}

    from app.models.base import MergeCandidateStatusEnum
    mc = MagicMock()
    mc.candidate_id = 1
    mc.vessel_a_id = 10
    mc.vessel_b_id = 20
    mc.confidence_score = 78
    mc.time_delta_hours = 12.0
    mc.distance_nm = 45.0
    mc.vessel_a_last_time = datetime(2026, 1, 15)
    mc.vessel_b_first_time = datetime(2026, 1, 17)
    mc.status = MergeCandidateStatusEnum.PENDING

    mock_query = MagicMock()
    mock_query.filter.return_value.order_by.return_value.all.return_value = [mc]
    mock_db.query.return_value = mock_query

    va = MagicMock()
    va.mmsi = "211379500"
    va.name = "TANKER ONE"
    va.flag = "LR"
    mock_db.query.return_value.get.return_value = va

    mock_merge.return_value = {"success": True, "merge_op_id": 1}

    with patch("app.cli._is_interactive", return_value=True):
        result = runner.invoke(app, ["check-vessels"])

    assert mc.status == MergeCandidateStatusEnum.ANALYST_MERGED


@patch("typer.prompt", return_value="r")
@patch("app.modules.identity_resolver.detect_merge_candidates")
@patch("app.database.SessionLocal")
def test_check_vessels_interactive_reject(mock_sl, mock_detect, mock_prompt):
    """Interactive reject updates candidate status."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_detect.return_value = {"auto_merged": 0, "candidates_created": 1, "skipped": 0}

    from app.models.base import MergeCandidateStatusEnum
    mc = MagicMock()
    mc.candidate_id = 1
    mc.vessel_a_id = 10
    mc.vessel_b_id = 20
    mc.confidence_score = 60
    mc.time_delta_hours = 24.0
    mc.distance_nm = 100.0
    mc.vessel_a_last_time = None
    mc.vessel_b_first_time = None
    mc.status = MergeCandidateStatusEnum.PENDING

    mock_query = MagicMock()
    mock_query.filter.return_value.order_by.return_value.all.return_value = [mc]
    mock_db.query.return_value = mock_query

    va = MagicMock()
    va.mmsi = "211379500"
    va.name = "TANKER"
    va.flag = "LR"
    mock_db.query.return_value.get.return_value = va

    with patch("app.cli._is_interactive", return_value=True):
        result = runner.invoke(app, ["check-vessels"])

    assert mc.status == MergeCandidateStatusEnum.REJECTED


@patch("typer.prompt", return_value="s")
@patch("app.modules.identity_resolver.detect_merge_candidates")
@patch("app.database.SessionLocal")
def test_check_vessels_interactive_skip(mock_sl, mock_detect, mock_prompt):
    """Interactive skip leaves candidate PENDING."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_detect.return_value = {"auto_merged": 0, "candidates_created": 1, "skipped": 0}

    from app.models.base import MergeCandidateStatusEnum
    mc = MagicMock()
    mc.candidate_id = 1
    mc.vessel_a_id = 10
    mc.vessel_b_id = 20
    mc.confidence_score = 55
    mc.time_delta_hours = 48.0
    mc.distance_nm = 200.0
    mc.vessel_a_last_time = None
    mc.vessel_b_first_time = None
    mc.status = MergeCandidateStatusEnum.PENDING

    mock_query = MagicMock()
    mock_query.filter.return_value.order_by.return_value.all.return_value = [mc]
    mock_db.query.return_value = mock_query

    va = MagicMock()
    va.mmsi = "999999999"
    va.name = "UNKNOWN"
    va.flag = "XX"
    mock_db.query.return_value.get.return_value = va

    with patch("app.cli._is_interactive", return_value=True):
        result = runner.invoke(app, ["check-vessels"])

    assert mc.status == MergeCandidateStatusEnum.PENDING


@patch("typer.prompt", return_value="q")
@patch("app.modules.identity_resolver.detect_merge_candidates")
@patch("app.database.SessionLocal")
def test_check_vessels_interactive_quit(mock_sl, mock_detect, mock_prompt):
    """Interactive quit exits loop, leaves candidates untouched."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_detect.return_value = {"auto_merged": 0, "candidates_created": 2, "skipped": 0}

    from app.models.base import MergeCandidateStatusEnum
    mc1 = MagicMock()
    mc1.candidate_id = 1
    mc1.vessel_a_id = 10
    mc1.vessel_b_id = 20
    mc1.confidence_score = 70
    mc1.time_delta_hours = 12.0
    mc1.distance_nm = 30.0
    mc1.vessel_a_last_time = None
    mc1.vessel_b_first_time = None
    mc1.status = MergeCandidateStatusEnum.PENDING

    mc2 = MagicMock()
    mc2.candidate_id = 2
    mc2.vessel_a_id = 30
    mc2.vessel_b_id = 40
    mc2.confidence_score = 60
    mc2.time_delta_hours = 24.0
    mc2.distance_nm = 50.0
    mc2.vessel_a_last_time = None
    mc2.vessel_b_first_time = None
    mc2.status = MergeCandidateStatusEnum.PENDING

    mock_query = MagicMock()
    mock_query.filter.return_value.order_by.return_value.all.return_value = [mc1, mc2]
    mock_db.query.return_value = mock_query

    va = MagicMock()
    va.mmsi = "211379500"
    va.name = "TANKER"
    va.flag = "LR"
    mock_db.query.return_value.get.return_value = va

    with patch("app.cli._is_interactive", return_value=True):
        result = runner.invoke(app, ["check-vessels"])

    assert mc1.status == MergeCandidateStatusEnum.PENDING
    assert mc2.status == MergeCandidateStatusEnum.PENDING


@patch("typer.prompt", return_value="m")
@patch("app.modules.identity_resolver.execute_merge")
@patch("app.modules.identity_resolver.detect_merge_candidates")
@patch("app.database.SessionLocal")
def test_check_vessels_merge_failure_preserves_status(mock_sl, mock_detect, mock_merge, mock_prompt):
    """Failed merge keeps candidate PENDING (not ANALYST_MERGED)."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_detect.return_value = {"auto_merged": 0, "candidates_created": 1, "skipped": 0}

    from app.models.base import MergeCandidateStatusEnum
    mc = MagicMock()
    mc.candidate_id = 1
    mc.vessel_a_id = 10
    mc.vessel_b_id = 20
    mc.confidence_score = 78
    mc.time_delta_hours = 12.0
    mc.distance_nm = 45.0
    mc.vessel_a_last_time = datetime(2026, 1, 15)
    mc.vessel_b_first_time = datetime(2026, 1, 17)
    mc.status = MergeCandidateStatusEnum.PENDING

    mock_query = MagicMock()
    mock_query.filter.return_value.order_by.return_value.all.return_value = [mc]
    mock_db.query.return_value = mock_query

    va = MagicMock()
    va.mmsi = "211379500"
    va.name = "TANKER"
    va.flag = "LR"
    mock_db.query.return_value.get.return_value = va

    mock_merge.return_value = {"success": False, "error": "Already absorbed"}

    with patch("app.cli._is_interactive", return_value=True):
        result = runner.invoke(app, ["check-vessels"])

    assert mc.status == MergeCandidateStatusEnum.PENDING


# ---------------------------------------------------------------------------
# open
# ---------------------------------------------------------------------------


@patch("uvicorn.run")
@patch("webbrowser.open")
@patch("threading.Thread")
def test_open_browser_thread(mock_thread_cls, mock_wb_open, mock_uvicorn_run):
    """open launches browser in thread before uvicorn."""
    mock_thread_instance = MagicMock()
    mock_thread_cls.return_value = mock_thread_instance

    result = runner.invoke(app, ["open"])
    mock_uvicorn_run.assert_called_once_with("app.main:app", host="127.0.0.1", port=8000)
    mock_thread_cls.assert_called_once()
    mock_thread_instance.start.assert_called_once()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@patch("app.database.SessionLocal")
def test_status_fresh_data(mock_sl):
    """status shows no staleness warning for recent data."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    recent = datetime.utcnow() - timedelta(hours=2)
    mock_db.query.return_value.count.return_value = 100
    mock_db.query.return_value.filter.return_value.count.return_value = 50
    mock_db.query.return_value.scalar.return_value = recent

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "OK" in result.output
    assert "days old" not in result.output


@patch("app.database.SessionLocal")
def test_status_stale_data(mock_sl):
    """status warns about stale data and suggests update."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    old = datetime.utcnow() - timedelta(days=5)
    mock_db.query.return_value.count.return_value = 500
    mock_db.query.return_value.filter.return_value.count.return_value = 200
    mock_db.query.return_value.scalar.return_value = old

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "days" in result.output.lower()
    assert "update" in result.output.lower()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@patch("app.database.SessionLocal")
def test_search_by_mmsi(mock_sl):
    """search --mmsi finds vessel and shows watchlist status."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    vessel = MagicMock()
    vessel.vessel_id = 1
    vessel.mmsi = "211379500"
    vessel.imo = "9123456"
    vessel.name = "SHADOW TANKER"
    vessel.flag = "LR"
    vessel.vessel_type = "Crude Oil Tanker"
    vessel.deadweight = 150000

    wl_entry = MagicMock()
    wl_entry.watchlist_source = "OFAC"

    last_point = MagicMock()
    last_point.timestamp_utc = datetime(2026, 2, 15, 12, 0)
    last_point.lat = 25.123
    last_point.lon = 56.789

    # Setup query chains
    mock_vessel_query = MagicMock()
    mock_vessel_query.filter.return_value.limit.return_value.all.return_value = [vessel]

    mock_wl_query = MagicMock()
    mock_wl_query.filter.return_value.all.return_value = [wl_entry]

    mock_ais_query = MagicMock()
    mock_ais_query.filter.return_value.order_by.return_value.first.return_value = last_point

    # Route different model queries
    def route_query(model):
        from app.models.vessel import Vessel
        from app.models.vessel_watchlist import VesselWatchlist
        from app.models.ais_point import AISPoint
        if model is Vessel:
            return mock_vessel_query
        elif model is VesselWatchlist:
            return mock_wl_query
        elif model is AISPoint:
            return mock_ais_query
        return MagicMock()

    mock_db.query.side_effect = route_query

    result = runner.invoke(app, ["search", "--mmsi", "211379500"])
    assert result.exit_code == 0
    assert "211379500" in result.output
    assert "WATCHLIST" in result.output
    assert "OFAC" in result.output


@patch("app.database.SessionLocal")
def test_search_no_results(mock_sl):
    """search with no matching vessels shows 'No vessels found'."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    mock_query = MagicMock()
    mock_query.filter.return_value.limit.return_value.all.return_value = []
    mock_db.query.return_value = mock_query

    result = runner.invoke(app, ["search", "--mmsi", "000000000"])
    assert result.exit_code == 0
    assert "No vessels found" in result.output


# ---------------------------------------------------------------------------
# help output
# ---------------------------------------------------------------------------


def test_help_shows_six_commands():
    """--help lists all 6 commands without panel grouping."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.output
    for cmd in ["start", "update", "check-vessels", "open", "status", "search"]:
        assert cmd in output, f"Command '{cmd}' not found in help output"
    # No panel grouping
    assert "Getting Started" not in output
    assert "Advanced" not in output


# ---------------------------------------------------------------------------
# corridor transaction semantics (source inspection)
# ---------------------------------------------------------------------------


def test_import_corridors_uses_flush():
    """_import_corridors uses db.flush() not db.commit() for atomicity."""
    from app import cli
    source = inspect.getsource(cli._import_corridors)
    assert "db.flush()" in source, "_import_corridors should use db.flush()"
    assert "db.commit()" not in source, "_import_corridors should NOT use db.commit()"


def test_import_corridors_rollback_on_error():
    """_import_corridors calls db.rollback() in its exception handler."""
    from app import cli
    source = inspect.getsource(cli._import_corridors)
    assert "db.rollback()" in source, "_import_corridors exception handler should rollback"
