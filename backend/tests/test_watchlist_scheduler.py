"""Tests for watchlist auto-update scheduler."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.modules.watchlist_scheduler import (
    SOURCES,
    WatchlistUpdateLog,
    _last_update,
    _should_update,
    run_watchlist_update,
    update_source,
)


@pytest.fixture(autouse=True)
def _clear_last_update():
    """Reset in-memory update tracker between tests."""
    _last_update.clear()
    yield
    _last_update.clear()


@pytest.fixture
def db(tmp_path):
    """Create an in-memory SQLite database with required tables."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine)
    session = Session()

    # Create minimal tables needed
    session.execute(
        text(
            "CREATE TABLE vessels ("
            "  vessel_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  mmsi VARCHAR(20),"
            "  imo VARCHAR(20),"
            "  name VARCHAR(200),"
            "  flag VARCHAR(10),"
            "  flag_risk_category VARCHAR(20),"
            "  vessel_type VARCHAR(50),"
            "  deadweight REAL,"
            "  callsign VARCHAR(20)"
            ")"
        )
    )
    session.execute(
        text(
            "CREATE TABLE vessel_watchlist ("
            "  watchlist_entry_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  vessel_id INTEGER NOT NULL,"
            "  watchlist_source VARCHAR(100) NOT NULL,"
            "  reason VARCHAR(500),"
            "  date_listed DATE,"
            "  source_url VARCHAR(500),"
            "  is_active BOOLEAN DEFAULT 1,"
            "  match_confidence INTEGER DEFAULT 0,"
            "  match_type VARCHAR(50) DEFAULT 'unknown',"
            "  UNIQUE(vessel_id, watchlist_source)"
            ")"
        )
    )
    session.execute(
        text(
            "CREATE TABLE vessel_history ("
            "  history_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  vessel_id INTEGER NOT NULL,"
            "  field_changed VARCHAR(50),"
            "  old_value TEXT,"
            "  new_value TEXT,"
            "  observed_at TIMESTAMP,"
            "  source VARCHAR(100)"
            ")"
        )
    )
    session.execute(
        text(
            "CREATE TABLE watchlist_update_log ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  source_name VARCHAR(100) NOT NULL,"
            "  updated_at TIMESTAMP NOT NULL,"
            "  status VARCHAR(20) NOT NULL,"
            "  added INTEGER DEFAULT 0,"
            "  removed INTEGER DEFAULT 0,"
            "  unchanged INTEGER DEFAULT 0,"
            "  error TEXT"
            ")"
        )
    )
    session.commit()
    yield session
    session.close()


class TestShouldUpdate:
    def test_no_prior_update_returns_true(self, db):
        assert _should_update(db, "OFAC_SDN", timedelta(days=1)) is True

    def test_recent_update_returns_false(self, db):
        _last_update["OFAC_SDN"] = datetime.now(UTC)
        assert _should_update(db, "OFAC_SDN", timedelta(days=1)) is False

    def test_old_update_returns_true(self, db):
        _last_update["OFAC_SDN"] = datetime.now(UTC) - timedelta(days=2)
        assert _should_update(db, "OFAC_SDN", timedelta(days=1)) is True

    def test_interval_respected_for_weekly(self, db):
        _last_update["KSE_SHADOW"] = datetime.now(UTC) - timedelta(days=3)
        assert _should_update(db, "KSE_SHADOW", timedelta(days=7)) is False


class TestUpdateSourceSkip:
    def test_skip_within_interval(self, db):
        _last_update["OFAC_SDN"] = datetime.now(UTC)
        ofac_cfg = SOURCES[0]
        result = update_source(db, ofac_cfg, force=False)
        assert result["status"] == "skipped"

    def test_force_ignores_interval(self, db):
        _last_update["OFAC_SDN"] = datetime.now(UTC)
        ofac_cfg = SOURCES[0]
        with patch("app.modules.watchlist_scheduler.update_source", wraps=update_source) as _:
            # We need to mock the fetch to avoid network calls
            with patch("app.modules.data_fetcher.fetch_ofac_sdn") as mock_fetch:
                mock_fetch.return_value = {"path": None, "status": "error", "error": "test"}
                result = update_source(db, ofac_cfg, force=True)
                # It should NOT skip — it tries to fetch (and gets an error from our mock)
                assert result["status"] == "error"


class TestUpdateSourceFetchError:
    def test_fetch_error_does_not_crash(self, db):
        ofac_cfg = SOURCES[0]
        with patch("app.modules.data_fetcher.fetch_ofac_sdn") as mock_fetch:
            mock_fetch.return_value = {
                "error": "connection timeout",
                "path": None,
                "status": "error",
            }
            result = update_source(db, ofac_cfg, force=True)
            assert result["status"] == "error"
            assert "timeout" in result["error"]


class TestUpdateSourceSuccess:
    def test_successful_load(self, db, tmp_path):
        """Verify that a successful loader run records the update."""
        ofac_cfg = SOURCES[0]

        dummy_file = tmp_path / "ofac_sdn_2026-03-06.csv"
        dummy_file.write_text("header\n")

        with (
            patch("app.modules.data_fetcher.fetch_ofac_sdn") as mock_fetch,
            patch("app.modules.watchlist_loader.load_ofac_sdn") as mock_loader,
        ):
            mock_fetch.return_value = {
                "path": str(dummy_file),
                "status": "downloaded",
                "error": None,
            }
            mock_loader.return_value = {"matched": 5, "unmatched": 1, "skipped": 0}
            result = update_source(db, ofac_cfg, force=True)

        assert result["status"] == "success"
        assert result["matched"] == 5
        assert "OFAC_SDN" in _last_update


class TestRunWatchlistUpdate:
    def test_runs_all_sources(self, db):
        """All 3 sources should produce a result (even if error due to no files)."""
        with (
            patch("app.modules.data_fetcher.fetch_ofac_sdn") as m1,
            patch("app.modules.data_fetcher.fetch_opensanctions_vessels") as m2,
        ):
            m1.return_value = {"path": None, "status": "error", "error": "no file"}
            m2.return_value = {"path": None, "status": "error", "error": "no file"}

            results = run_watchlist_update(db, force=True)

        assert len(results) == 3
        source_names = {r["source"] for r in results}
        assert source_names == {"OFAC_SDN", "OPENSANCTIONS", "KSE_SHADOW"}

    def test_filter_by_source(self, db):
        with patch("app.modules.data_fetcher.fetch_ofac_sdn") as m1:
            m1.return_value = {"path": None, "status": "error", "error": "no file"}
            results = run_watchlist_update(db, force=True, sources=["OFAC_SDN"])

        assert len(results) == 1
        assert results[0]["source"] == "OFAC_SDN"

    def test_one_failure_doesnt_block_others(self, db):
        """If OFAC fails, OpenSanctions should still be attempted."""
        with (
            patch("app.modules.data_fetcher.fetch_ofac_sdn") as m1,
            patch("app.modules.data_fetcher.fetch_opensanctions_vessels") as m2,
        ):
            m1.side_effect = RuntimeError("network down")
            m2.return_value = {"path": None, "status": "error", "error": "no file"}

            results = run_watchlist_update(db, force=True)

        # All 3 should have results
        assert len(results) == 3
        ofac = next(r for r in results if r["source"] == "OFAC_SDN")
        assert ofac["status"] == "error"
        # OpenSanctions was still attempted
        os_result = next(r for r in results if r["source"] == "OPENSANCTIONS")
        assert os_result["status"] == "error"  # no file, but it was attempted


class TestWatchlistUpdateLog:
    def test_record_and_retrieve(self, db):
        WatchlistUpdateLog.record_update(
            db, "OFAC_SDN", "success", added=5, removed=1, unchanged=100
        )
        last = WatchlistUpdateLog.get_last_update(db, "OFAC_SDN")
        assert last is not None
        assert (datetime.now(UTC) - last).total_seconds() < 5

    def test_no_record_returns_none(self, db):
        last = WatchlistUpdateLog.get_last_update(db, "NONEXISTENT")
        assert last is None
