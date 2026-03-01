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


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._update_stream_ais")
@patch("app.cli._update_fetch_watchlists")
@patch("app.modules.digitraffic_client.fetch_digitraffic_ais")
@patch("app.database.SessionLocal")
def test_update_calls_digitraffic_when_enabled(
    mock_sl, mock_digi, mock_fetch, mock_stream, mock_discover, mock_summary, mock_next
):
    """update calls Digitraffic when DIGITRAFFIC_ENABLED=True."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_digi.return_value = {"points_ingested": 42, "vessels_seen": 5}

    with patch("app.config.settings") as mock_settings:
        mock_settings.AISSTREAM_API_KEY = "test-key"
        mock_settings.DIGITRAFFIC_ENABLED = True
        mock_settings.KYSTVERKET_ENABLED = False
        mock_settings.AISHUB_ENABLED = False
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    mock_digi.assert_called_once_with(mock_db)
    assert "Digitraffic: 42 points" in result.output


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._update_stream_ais")
@patch("app.cli._update_fetch_watchlists")
@patch("app.modules.kystverket_client.stream_kystverket")
@patch("app.database.SessionLocal")
def test_update_calls_kystverket_when_enabled(
    mock_sl, mock_kyst, mock_fetch, mock_stream, mock_discover, mock_summary, mock_next
):
    """update calls Kystverket when KYSTVERKET_ENABLED=True."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_kyst.return_value = {"points_ingested": 100, "vessels_seen": 10}

    with patch("app.config.settings") as mock_settings:
        mock_settings.AISSTREAM_API_KEY = "test-key"
        mock_settings.DIGITRAFFIC_ENABLED = False
        mock_settings.KYSTVERKET_ENABLED = True
        mock_settings.AISHUB_ENABLED = False
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    mock_kyst.assert_called_once()
    assert "Kystverket: 100 points" in result.output


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._update_stream_ais")
@patch("app.cli._update_fetch_watchlists")
@patch("app.modules.aishub_client.ingest_aishub_positions")
@patch("app.modules.aishub_client.fetch_area_positions")
@patch("app.modules.aisstream_client.get_corridor_bounding_boxes")
@patch("app.database.SessionLocal")
def test_update_calls_aishub_when_enabled(
    mock_sl, mock_boxes, mock_fetch_pos, mock_ingest_pos,
    mock_fetch, mock_stream, mock_discover, mock_summary, mock_next
):
    """update calls AISHub when AISHUB_ENABLED=True, with correct bbox tuple format."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_boxes.return_value = [[[10.0, 20.0], [30.0, 40.0]]]
    mock_fetch_pos.return_value = [{"mmsi": "123456789"}]
    mock_ingest_pos.return_value = {"stored": 15, "skipped": 2}

    with patch("app.config.settings") as mock_settings:
        mock_settings.AISSTREAM_API_KEY = "test-key"
        mock_settings.DIGITRAFFIC_ENABLED = False
        mock_settings.KYSTVERKET_ENABLED = False
        mock_settings.AISHUB_ENABLED = True
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    # Verify bbox tuple format: (lat_min, lon_min, lat_max, lon_max)
    mock_fetch_pos.assert_called_once_with((10.0, 20.0, 30.0, 40.0))
    assert "AISHub: 15 positions" in result.output


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
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


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._update_stream_ais")
@patch("app.cli._update_fetch_watchlists")
@patch("app.modules.digitraffic_client.fetch_digitraffic_ais")
@patch("app.database.SessionLocal")
def test_multi_source_failure_does_not_block_detection(
    mock_sl, mock_digi, mock_fetch, mock_stream, mock_discover, mock_summary, mock_next
):
    """Multi-source failure prints warning but detection still runs."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_digi.side_effect = RuntimeError("connection refused")

    with patch("app.config.settings") as mock_settings:
        mock_settings.AISSTREAM_API_KEY = "test-key"
        mock_settings.DIGITRAFFIC_ENABLED = True
        mock_settings.KYSTVERKET_ENABLED = False
        mock_settings.AISHUB_ENABLED = False
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    assert "connection refused" in result.output
    mock_discover.assert_called_once()


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._update_fetch_watchlists")
@patch("app.modules.digitraffic_client.fetch_digitraffic_ais")
@patch("app.database.SessionLocal")
def test_multi_source_runs_without_aisstream_key(
    mock_sl, mock_digi, mock_fetch, mock_discover, mock_summary, mock_next
):
    """Multi-source collectors run even when no AISSTREAM_API_KEY is set."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_digi.return_value = {"points_ingested": 10, "vessels_seen": 2}

    with patch("app.config.settings") as mock_settings:
        mock_settings.AISSTREAM_API_KEY = ""
        mock_settings.DIGITRAFFIC_ENABLED = True
        mock_settings.KYSTVERKET_ENABLED = False
        mock_settings.AISHUB_ENABLED = False
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    mock_digi.assert_called_once_with(mock_db)
    assert "Digitraffic: 10 points" in result.output


# ---------------------------------------------------------------------------
# Backfill command
# ---------------------------------------------------------------------------


@patch("app.database.SessionLocal")
def test_backfill_validates_date_order(mock_sl):
    """backfill exits with code 1 if start > end."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    result = runner.invoke(app, ["backfill", "--start", "2025-12-31", "--end", "2025-12-01"])
    assert result.exit_code == 1
    assert "before or equal" in result.output


def test_backfill_requires_start_and_end():
    """backfill without --start and --end shows error."""
    result = runner.invoke(app, ["backfill"])
    assert result.exit_code != 0


@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.modules.noaa_client.fetch_and_import_noaa")
@patch("app.database.SessionLocal")
def test_backfill_calls_noaa_import(mock_sl, mock_noaa, mock_discover):
    """backfill calls fetch_and_import_noaa with correct dates."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_noaa.return_value = {
        "dates_attempted": 5, "dates_downloaded": 5,
        "total_accepted": 1000, "total_rows": 1500, "dates_failed": [],
    }

    result = runner.invoke(app, ["backfill", "--start", "2025-12-01", "--end", "2025-12-05"])
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
    """backfill runs detection after import by default."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_noaa.return_value = {
        "dates_attempted": 1, "dates_downloaded": 1,
        "total_accepted": 100, "total_rows": 150, "dates_failed": [],
    }

    result = runner.invoke(app, ["backfill", "--start", "2025-12-01", "--end", "2025-12-01"])
    assert result.exit_code == 0
    mock_discover.assert_called_once()


@patch("app.modules.noaa_client.fetch_and_import_noaa")
@patch("app.database.SessionLocal")
def test_backfill_no_detect_skips_detection(mock_sl, mock_noaa):
    """backfill --no-detect skips detection after import."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_noaa.return_value = {
        "dates_attempted": 1, "dates_downloaded": 1,
        "total_accepted": 50, "total_rows": 80, "dates_failed": [],
    }

    with patch("app.modules.dark_vessel_discovery.discover_dark_vessels") as mock_discover:
        result = runner.invoke(app, ["backfill", "--start", "2025-12-01", "--end", "2025-12-01", "--no-detect"])

    assert result.exit_code == 0
    mock_discover.assert_not_called()


@patch("app.database.SessionLocal")
def test_backfill_unknown_source_exits(mock_sl):
    """backfill with unknown source exits with code 1."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    result = runner.invoke(app, ["backfill", "--start", "2025-12-01", "--end", "2025-12-31", "--source", "unknown"])
    assert result.exit_code == 1
    assert "Unknown source" in result.output


# ---------------------------------------------------------------------------
# Help and command listing
# ---------------------------------------------------------------------------


def test_help_shows_all_commands():
    """--help lists all 10 commands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    output = result.output
    for cmd in ["start", "update", "check-vessels", "open", "status", "search",
                "rescore", "evaluate-detector", "confirm-detector", "backfill"]:
        assert cmd in output, f"Command '{cmd}' not found in help output"


# ---------------------------------------------------------------------------
# start --demo skips multi-source
# ---------------------------------------------------------------------------


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._load_sample_data")
@patch("app.cli._import_corridors")
@patch("app.cli._is_first_run", return_value=True)
@patch("app.database.SessionLocal")
@patch("app.database.init_db")
def test_start_demo_skips_multi_source(
    mock_init, mock_sl, mock_first_run, mock_corridors, mock_sample,
    mock_discover, mock_summary, mock_next
):
    """start --demo does not call _collect_multi_source_ais."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_db.query.return_value.count.return_value = 1  # ports already seeded

    with patch("app.cli._collect_multi_source_ais") as mock_multi:
        result = runner.invoke(app, ["start", "--demo"])

    assert result.exit_code == 0
    mock_multi.assert_not_called()


# ---------------------------------------------------------------------------
# --check-identity flag
# ---------------------------------------------------------------------------


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._update_fetch_watchlists")
@patch("app.cli._collect_multi_source_ais")
@patch("app.modules.identity_resolver.diagnose_merge_readiness")
@patch("app.database.SessionLocal")
def test_update_check_identity_prints_diagnostic(
    mock_sl, mock_diag, mock_multi, mock_fetch, mock_discover, mock_summary, mock_next
):
    """update --check-identity prints merge readiness diagnostic."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db
    mock_diag.return_value = {"vessels_tracked": 100, "merge_candidates": 5, "auto_mergeable": 2}

    with patch("app.config.settings") as mock_settings:
        mock_settings.AISSTREAM_API_KEY = ""
        mock_settings.DIGITRAFFIC_ENABLED = False
        mock_settings.KYSTVERKET_ENABLED = False
        mock_settings.AISHUB_ENABLED = False
        result = runner.invoke(app, ["update", "--check-identity"])

    assert result.exit_code == 0
    assert "Merge Readiness Diagnostic" in result.output
    mock_diag.assert_called_once_with(mock_db)


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._update_fetch_watchlists")
@patch("app.cli._collect_multi_source_ais")
@patch("app.database.SessionLocal")
def test_update_check_identity_graceful_without_agent_c(
    mock_sl, mock_multi, mock_fetch, mock_discover, mock_summary, mock_next
):
    """update --check-identity handles missing diagnose_merge_readiness gracefully."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    with patch("app.config.settings") as mock_settings:
        mock_settings.AISSTREAM_API_KEY = ""
        mock_settings.DIGITRAFFIC_ENABLED = False
        mock_settings.KYSTVERKET_ENABLED = False
        mock_settings.AISHUB_ENABLED = False
        # Make the import fail
        with patch("app.cli.update") as orig_update:
            # We need to test the real function, not a mock. Let's use a different approach.
            pass

    # Use the real update with a mock that simulates ImportError
    with patch("app.config.settings") as mock_settings, \
         patch.dict("sys.modules", {"app.modules.identity_resolver": MagicMock(
             diagnose_merge_readiness=MagicMock(side_effect=AttributeError("no such function"))
         )}):
        mock_settings.AISSTREAM_API_KEY = ""
        mock_settings.DIGITRAFFIC_ENABLED = False
        mock_settings.KYSTVERKET_ENABLED = False
        mock_settings.AISHUB_ENABLED = False
        result = runner.invoke(app, ["update", "--check-identity"])

    assert result.exit_code == 0
    assert "not available" in result.output


@patch("app.cli._print_next_steps")
@patch("app.cli._print_summary")
@patch("app.modules.dark_vessel_discovery.discover_dark_vessels")
@patch("app.cli._update_fetch_watchlists")
@patch("app.cli._collect_multi_source_ais")
@patch("app.database.SessionLocal")
def test_update_without_check_identity_no_merge_output(
    mock_sl, mock_multi, mock_fetch, mock_discover, mock_summary, mock_next
):
    """update without --check-identity does not show merge diagnostic."""
    mock_db = MagicMock()
    mock_sl.return_value = mock_db

    with patch("app.config.settings") as mock_settings:
        mock_settings.AISSTREAM_API_KEY = ""
        mock_settings.DIGITRAFFIC_ENABLED = False
        mock_settings.KYSTVERKET_ENABLED = False
        mock_settings.AISHUB_ENABLED = False
        result = runner.invoke(app, ["update"])

    assert result.exit_code == 0
    assert "Merge Readiness Diagnostic" not in result.output
    assert "not available" not in result.output
