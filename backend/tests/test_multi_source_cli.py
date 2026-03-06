"""Tests for multi-source AIS CLI integration and backfill command."""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch, call

import pytest
from typer.testing import CliRunner

from app.cli import app


runner = CliRunner()


# ---------------------------------------------------------------------------
# Multi-source collection in update
# ---------------------------------------------------------------------------


@patch("app.cli_helpers._print_next_steps")
@patch("app.cli_helpers._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli_helpers._update_fetch_watchlists")
@patch("app.modules.collection_scheduler.CollectionScheduler")
@patch("app.database.SessionLocal")
def test_update_calls_scheduler_when_online(
    mock_sl, mock_scheduler, mock_fetch, mock_discover, mock_summary, mock_next
):
    """update starts CollectionScheduler when not --offline."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_instance = MagicMock()
    mock_scheduler.return_value = mock_instance

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    mock_scheduler.assert_called_once()
    mock_instance.start.assert_called_once()


@patch("app.cli_helpers._print_next_steps")
@patch("app.cli_helpers._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli_helpers._update_fetch_watchlists")
@patch("app.modules.collection_scheduler.CollectionScheduler")
@patch("app.database.SessionLocal")
def test_update_scheduler_failure_does_not_block_detection(
    mock_sl, mock_scheduler, mock_fetch, mock_discover, mock_summary, mock_next
):
    """CollectionScheduler failure prints warning but detection still runs."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_instance = MagicMock()
    mock_instance.start.side_effect = RuntimeError("connection refused")
    mock_scheduler.return_value = mock_instance

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    assert "connection refused" in result.output
    mock_discover.assert_called_once()


@patch("app.cli_helpers._print_next_steps")
@patch("app.cli_helpers._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli_helpers._update_fetch_watchlists")
@patch("app.modules.collection_scheduler.CollectionScheduler")
@patch("app.database.SessionLocal")
def test_update_passes_stream_time_to_scheduler(
    mock_sl, mock_scheduler, mock_fetch, mock_discover, mock_summary, mock_next
):
    """update --stream-time is parsed and passed as duration_seconds to scheduler.start."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_instance = MagicMock()
    mock_scheduler.return_value = mock_instance

    result = runner.invoke(app, ["update", "--stream-time", "5m"])

    assert result.exit_code == 0
    mock_instance.start.assert_called_once_with(duration_seconds=300)


@patch("app.cli_helpers._print_next_steps")
@patch("app.cli_helpers._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.database.SessionLocal")
def test_update_offline_skips_multi_source(mock_sl, mock_discover, mock_summary, mock_next):
    """update --offline does not call any multi-source collectors."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    with patch("app.modules.digitraffic_client.fetch_digitraffic_ais") as mock_digi, \
         patch("app.modules.kystverket_client.stream_kystverket") as mock_kyst:
        result = runner.invoke(app, ["update", "--offline"])

    assert result.exit_code == 0
    mock_digi.assert_not_called()
    mock_kyst.assert_not_called()


@patch("app.cli_helpers._print_next_steps")
@patch("app.cli_helpers._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli_helpers._update_fetch_watchlists")
@patch("app.modules.collection_scheduler.CollectionScheduler")
@patch("app.database.SessionLocal")
def test_update_runs_detection_after_collection(
    mock_sl, mock_scheduler, mock_fetch, mock_discover, mock_summary, mock_next
):
    """update always runs detection after collection."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    mock_discover.assert_called_once()


# ---------------------------------------------------------------------------
# Backfill command
# ---------------------------------------------------------------------------


@patch("app.database.SessionLocal")
def test_backfill_validates_date_order(mock_sl):
    """history backfill exits with code 1 if start > end."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    result = runner.invoke(app, ["history", "backfill", "--source", "noaa", "--start", "2025-12-31", "--end", "2025-12-01"])
    assert result.exit_code == 1
    assert "before or equal" in result.output


def test_backfill_requires_start_and_end():
    """history backfill without --start/--end and no --days shows error."""
    result = runner.invoke(app, ["history", "backfill", "--source", "noaa"])
    assert result.exit_code != 0


@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.modules.noaa_client.fetch_and_import_noaa")
@patch("app.database.SessionLocal")
def test_backfill_calls_noaa_import(mock_sl, mock_noaa, mock_discover):
    """history backfill calls fetch_and_import_noaa with correct dates."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_noaa.return_value = {
        "dates_attempted": 5, "dates_downloaded": 5,
        "total_accepted": 1000, "total_rows": 1500, "dates_failed": [],
    }

    result = runner.invoke(app, ["history", "backfill", "--source", "noaa", "--start", "2025-12-01", "--end", "2025-12-05"])
    assert result.exit_code == 0
    mock_noaa.assert_called_once_with(
        mock_db,
        start_date=date(2025, 12, 1),
        end_date=date(2025, 12, 5),
        corridor_filter=True,
    )
    assert "1,000 positions" in result.output


@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.modules.noaa_client.fetch_and_import_noaa")
@patch("app.database.SessionLocal")
def test_backfill_runs_detection_by_default(mock_sl, mock_noaa, mock_discover):
    """history backfill runs detection after import by default."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_noaa.return_value = {
        "dates_attempted": 1, "dates_downloaded": 1,
        "total_accepted": 100, "total_rows": 150, "dates_failed": [],
    }

    result = runner.invoke(app, ["history", "backfill", "--source", "noaa", "--start", "2025-12-01", "--end", "2025-12-01"])
    assert result.exit_code == 0
    mock_discover.assert_called_once()


@patch("app.modules.noaa_client.fetch_and_import_noaa")
@patch("app.database.SessionLocal")
def test_backfill_no_detect_skips_detection(mock_sl, mock_noaa):
    """history backfill --no-detect skips detection after import."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_noaa.return_value = {
        "dates_attempted": 1, "dates_downloaded": 1,
        "total_accepted": 50, "total_rows": 80, "dates_failed": [],
    }

    with patch("app.modules.dark_vessel_discovery.discover_dark_vessels") as mock_discover:
        result = runner.invoke(app, ["history", "backfill", "--source", "noaa", "--start", "2025-12-01", "--end", "2025-12-01", "--no-detect"])

    assert result.exit_code == 0
    mock_discover.assert_not_called()


@patch("app.database.SessionLocal")
def test_backfill_unknown_source_exits(mock_sl):
    """history backfill with unknown source exits with code 1."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    result = runner.invoke(app, ["history", "backfill", "--source", "unknown", "--start", "2025-12-01", "--end", "2025-12-31"])
    assert result.exit_code == 1
    assert "Unknown source" in result.output


# ---------------------------------------------------------------------------
# Help and command listing
# ---------------------------------------------------------------------------


def test_help_shows_core_commands():
    """--help lists all core commands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.output
    for cmd in ["start", "update", "check-vessels", "open", "status", "search",
                "rescore", "evaluate-detector", "confirm-detector"]:
        assert cmd in output, f"Command '{cmd}' not found in help output"


# ---------------------------------------------------------------------------
# start --demo skips collection
# ---------------------------------------------------------------------------


@patch("app.cli_helpers._print_next_steps")
@patch("app.cli_helpers._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli_helpers._load_sample_data")
@patch("app.cli_helpers._import_corridors")
@patch("app.cli_helpers._is_first_run", return_value=True)
@patch("app.database.SessionLocal")
@patch("app.database.init_db")
def test_start_demo_skips_collection(
    mock_init, mock_sl, mock_first_run, mock_corridors, mock_sample,
    mock_discover, mock_summary, mock_next
):
    """start --demo does not run CollectionScheduler."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_db.query.return_value.count.return_value = 1  # ports already seeded

    with patch("app.modules.collection_scheduler.CollectionScheduler") as mock_scheduler:
        result = runner.invoke(app, ["start", "--demo"])

    assert result.exit_code == 0
    mock_scheduler.assert_not_called()


# ---------------------------------------------------------------------------
# --check-identity flag
# ---------------------------------------------------------------------------


@patch("app.cli_helpers._print_next_steps")
@patch("app.cli_helpers._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli_helpers._update_fetch_watchlists")
@patch("app.modules.collection_scheduler.CollectionScheduler")
@patch("app.modules.identity_resolver.diagnose_merge_readiness")
@patch("app.database.SessionLocal")
def test_update_check_identity_prints_diagnostic(
    mock_sl, mock_diag, mock_scheduler, mock_fetch, mock_discover, mock_summary, mock_next
):
    """update --check-identity prints merge readiness diagnostic."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_diag.return_value = {"vessels_tracked": 100, "merge_candidates": 5, "auto_mergeable": 2}

    result = runner.invoke(app, ["update", "--check-identity"])

    assert result.exit_code == 0
    assert "Merge Readiness Diagnostic" in result.output
    mock_diag.assert_called_once_with(mock_db)


@patch("app.cli_helpers._print_next_steps")
@patch("app.cli_helpers._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli_helpers._update_fetch_watchlists")
@patch("app.modules.collection_scheduler.CollectionScheduler")
@patch("app.database.SessionLocal")
def test_update_check_identity_graceful_without_agent_c(
    mock_sl, mock_scheduler, mock_fetch, mock_discover, mock_summary, mock_next
):
    """update --check-identity handles missing diagnose_merge_readiness gracefully."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    with patch.dict("sys.modules", {"app.modules.identity_resolver": MagicMock(
        diagnose_merge_readiness=MagicMock(side_effect=AttributeError("no such function"))
    )}):
        result = runner.invoke(app, ["update", "--check-identity"])

    assert result.exit_code == 0
    assert "not available" in result.output


@patch("app.cli_helpers._print_next_steps")
@patch("app.cli_helpers._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli_helpers._update_fetch_watchlists")
@patch("app.modules.collection_scheduler.CollectionScheduler")
@patch("app.database.SessionLocal")
def test_update_without_check_identity_no_merge_output(
    mock_sl, mock_scheduler, mock_fetch, mock_discover, mock_summary, mock_next
):
    """update without --check-identity does not show merge diagnostic."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    assert "Merge Readiness Diagnostic" not in result.output
    assert "not available" not in result.output
