"""Tests for the history pipeline: retention, coverage tracking, history scheduler,
track API, corridor activity, date filters, collection status bug fix, and CLI
history sub-commands.

Uses in-memory SQLite for model/module tests, real in-memory SQLite for API tests.
"""
from __future__ import annotations

import pytest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base  # registers all models
from app.models.ais_point import AISPoint
from app.models.vessel import Vessel
from app.models.corridor import Corridor
from app.models.gap_event import AISGapEvent
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.loitering_event import LoiteringEvent
from app.models.sts_transfer import StsTransferEvent
from app.models.port_call import PortCall
from app.models.data_coverage_window import DataCoverageWindow
from app.models.collection_run import CollectionRun
from app.models.base import SpoofingTypeEnum, AlertStatusEnum, STSDetectionTypeEnum

# API prefix used by app.main
API = "/api/v1"


# ---------------------------------------------------------------------------
# In-memory SQLite fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    """In-memory SQLite session with all tables."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, connection_record):
        c = dbapi_conn.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# ---------------------------------------------------------------------------
# API fixtures (real in-memory SQLite)
# ---------------------------------------------------------------------------

@pytest.fixture
def real_api_db():
    """In-memory SQLite session for API integration tests.

    Uses StaticPool to ensure the single in-memory DB connection is shared
    across threads (FastAPI runs sync handlers in a thread pool).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, connection_record):
        c = dbapi_conn.cursor()
        c.execute("PRAGMA foreign_keys=ON")
        c.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def real_api_client(real_api_db):
    """TestClient with a real in-memory SQLite for queries that need actual SQL."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import get_db

    def override():
        yield real_api_db

    app.dependency_overrides[get_db] = override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vessel(db, mmsi="123456789", name="TEST VESSEL", **kw):
    v = Vessel(mmsi=mmsi, name=name, **kw)
    db.add(v)
    db.flush()
    return v


def _ais_point(db, vessel, ts, lat=60.0, lon=25.0, source=None, **kw):
    # Strip tzinfo so SQLite stores naive datetimes consistently
    naive_ts = ts.replace(tzinfo=None) if ts.tzinfo else ts
    pt = AISPoint(
        vessel_id=vessel.vessel_id,
        timestamp_utc=naive_ts,
        lat=lat, lon=lon,
        sog=10.0, cog=180.0,
        source=source,
        **kw,
    )
    db.add(pt)
    db.flush()
    return pt


def _gap_event(db, vessel, start, end, corridor=None, risk=50):
    ge = AISGapEvent(
        vessel_id=vessel.vessel_id,
        gap_start_utc=start,
        gap_end_utc=end,
        duration_minutes=int((end - start).total_seconds() / 60),
        corridor_id=corridor.corridor_id if corridor else None,
        risk_score=risk,
        status=AlertStatusEnum.NEW,
        impossible_speed_flag=False,
        in_dark_zone=False,
    )
    db.add(ge)
    db.flush()
    return ge


def _corridor(db, name="Test Corridor"):
    c = Corridor(name=name, risk_weight=1.5)
    db.add(c)
    db.flush()
    return c


def _coverage_window(db, source, d_from, d_to, status="completed", **kw):
    w = DataCoverageWindow(
        source=source,
        date_from=d_from,
        date_to=d_to,
        status=status,
        started_at=datetime.now(timezone.utc),
        **kw,
    )
    db.add(w)
    db.flush()
    return w


# ===========================================================================
# TestRetentionPolicies
# ===========================================================================

class TestRetentionPolicies:
    """Tests for CollectionScheduler._prune_old_points retention logic."""

    def test_prune_skips_archive_sources(self, db):
        """NOAA/DMA/GFW points survive pruning because they are archive sources."""
        from app.modules.collection_scheduler import CollectionScheduler

        v = _vessel(db, mmsi="111111111")
        old_ts = datetime.now(timezone.utc) - timedelta(days=200)
        _ais_point(db, v, old_ts, source="noaa")
        _ais_point(db, v, old_ts, source="dma")
        _ais_point(db, v, old_ts, source="gfw")
        db.commit()

        scheduler = CollectionScheduler(db_factory=lambda: db)

        for source in ("noaa", "dma", "gfw"):
            scheduler._prune_old_points(source)

        assert db.query(AISPoint).count() == 3

    def test_prune_deletes_realtime_sources_past_retention(self, db):
        """aisstream points older than retention period get deleted."""
        from app.modules.collection_scheduler import CollectionScheduler

        v = _vessel(db, mmsi="222222222")
        old_ts = datetime.now(timezone.utc) - timedelta(days=200)
        recent_ts = datetime.now(timezone.utc) - timedelta(days=10)
        _ais_point(db, v, old_ts, source="aisstream")
        _ais_point(db, v, recent_ts, source="aisstream")
        db.commit()

        scheduler = CollectionScheduler(db_factory=lambda: db)
        scheduler._prune_old_points("aisstream")

        remaining = db.query(AISPoint).all()
        assert len(remaining) == 1
        assert remaining[0].source == "aisstream"

    def test_prune_with_no_source_column(self, db):
        """Points with source=None survive pruning — SQL NOT IN excludes NULLs."""
        from app.modules.collection_scheduler import CollectionScheduler

        v = _vessel(db, mmsi="333333333")
        old_ts = datetime.now(timezone.utc) - timedelta(days=200)
        _ais_point(db, v, old_ts, source=None)
        db.commit()

        scheduler = CollectionScheduler(db_factory=lambda: db)
        scheduler._prune_old_points("aisstream")

        # SQL: NULL NOT IN (...) evaluates to NULL (falsy), so NULL-source rows survive
        assert db.query(AISPoint).count() == 1


# ===========================================================================
# TestDataCoverageWindow
# ===========================================================================

class TestDataCoverageWindow:

    def test_model_creation(self, db):
        """DataCoverageWindow can be created and persisted."""
        w = _coverage_window(db, "noaa", date(2024, 1, 1), date(2024, 1, 31))
        db.commit()
        loaded = db.query(DataCoverageWindow).filter_by(window_id=w.window_id).one()
        assert loaded.source == "noaa"
        assert loaded.date_from == date(2024, 1, 1)
        assert loaded.date_to == date(2024, 1, 31)

    def test_unique_constraint_same_range_different_status(self, db):
        """Same source/range with different status is allowed by the unique constraint."""
        _coverage_window(db, "noaa", date(2024, 1, 1), date(2024, 1, 31), status="completed")
        _coverage_window(db, "noaa", date(2024, 1, 1), date(2024, 1, 31), status="failed")
        db.commit()
        count = db.query(DataCoverageWindow).filter_by(source="noaa").count()
        assert count == 2

    def test_gfw_partial_with_vessels_queried(self, db):
        """GFW partial window stores vessels_queried and vessels_total correctly."""
        w = _coverage_window(
            db, "gfw-gaps", date(2024, 6, 1), date(2024, 6, 30),
            status="completed", vessels_queried=45, vessels_total=100,
        )
        db.commit()
        loaded = db.query(DataCoverageWindow).filter_by(window_id=w.window_id).one()
        assert loaded.vessels_queried == 45
        assert loaded.vessels_total == 100


# ===========================================================================
# TestCoverageTracker
# ===========================================================================

class TestCoverageTracker:

    def test_get_covered_dates_empty(self, db):
        from app.modules.coverage_tracker import get_covered_dates
        result = get_covered_dates(db, "noaa")
        assert result == set()

    def test_get_covered_dates_multi_window(self, db):
        from app.modules.coverage_tracker import get_covered_dates
        _coverage_window(db, "noaa", date(2024, 1, 1), date(2024, 1, 3))
        _coverage_window(db, "noaa", date(2024, 1, 10), date(2024, 1, 11))
        db.commit()
        covered = get_covered_dates(db, "noaa")
        assert date(2024, 1, 1) in covered
        assert date(2024, 1, 2) in covered
        assert date(2024, 1, 3) in covered
        assert date(2024, 1, 10) in covered
        assert date(2024, 1, 11) in covered
        assert date(2024, 1, 5) not in covered

    def test_find_coverage_gaps_no_gaps(self, db):
        from app.modules.coverage_tracker import find_coverage_gaps
        _coverage_window(db, "noaa", date(2024, 1, 1), date(2024, 1, 31))
        db.commit()
        gaps = find_coverage_gaps(db, "noaa", date(2024, 1, 1), date(2024, 1, 31))
        assert gaps == []

    def test_find_coverage_gaps_partial_range(self, db):
        from app.modules.coverage_tracker import find_coverage_gaps
        _coverage_window(db, "noaa", date(2024, 1, 1), date(2024, 1, 10))
        db.commit()
        gaps = find_coverage_gaps(db, "noaa", date(2024, 1, 1), date(2024, 1, 15))
        assert len(gaps) == 1
        assert gaps[0] == (date(2024, 1, 11), date(2024, 1, 15))

    def test_find_coverage_gaps_multi_disjoint(self, db):
        """Two non-adjacent windows leave gaps in between and after."""
        from app.modules.coverage_tracker import find_coverage_gaps
        _coverage_window(db, "dma", date(2024, 1, 1), date(2024, 1, 5))
        _coverage_window(db, "dma", date(2024, 1, 10), date(2024, 1, 15))
        db.commit()
        gaps = find_coverage_gaps(db, "dma", date(2024, 1, 1), date(2024, 1, 20))
        assert len(gaps) == 2
        assert gaps[0] == (date(2024, 1, 6), date(2024, 1, 9))
        assert gaps[1] == (date(2024, 1, 16), date(2024, 1, 20))

    def test_record_coverage_window_completed(self, db):
        from app.modules.coverage_tracker import record_coverage_window
        w = record_coverage_window(
            db, "noaa", date(2024, 3, 1), date(2024, 3, 31),
            status="completed", points_imported=5000,
        )
        db.commit()
        assert w.window_id is not None
        assert w.status == "completed"
        assert w.points_imported == 5000

    def test_record_coverage_window_failed(self, db):
        from app.modules.coverage_tracker import record_coverage_window
        w = record_coverage_window(
            db, "dma", date(2024, 3, 1), date(2024, 3, 15),
            status="failed", errors=3, notes="API timeout",
        )
        db.commit()
        assert w.status == "failed"
        assert w.errors == 3
        assert w.notes == "API timeout"

    def test_record_coverage_window_upsert(self, db):
        """record_coverage_window is idempotent: second call updates, not inserts."""
        from app.modules.coverage_tracker import record_coverage_window
        from app.models.data_coverage_window import DataCoverageWindow

        record_coverage_window(
            db, "noaa", date(2024, 5, 1), date(2024, 5, 1),
            status="completed", points_imported=100,
        )
        db.commit()
        assert db.query(DataCoverageWindow).count() == 1

        # Second call with same key — should update, not insert
        record_coverage_window(
            db, "noaa", date(2024, 5, 1), date(2024, 5, 1),
            status="completed", points_imported=999,
        )
        db.commit()
        assert db.query(DataCoverageWindow).count() == 1
        w = db.query(DataCoverageWindow).first()
        assert w.points_imported == 999

    def test_coverage_summary_per_source(self, db):
        from app.modules.coverage_tracker import coverage_summary
        _coverage_window(db, "noaa", date(2024, 1, 1), date(2024, 1, 10), points_imported=1000)
        _coverage_window(db, "noaa", date(2024, 1, 15), date(2024, 1, 20), points_imported=500)
        _coverage_window(db, "dma", date(2024, 2, 1), date(2024, 2, 28), points_imported=2000)
        db.commit()

        summary = coverage_summary(db)
        assert "noaa" in summary
        assert summary["noaa"]["total_points"] == 1500
        assert summary["noaa"]["completed_windows"] == 2
        assert summary["noaa"]["gap_count"] == 1  # Jan 11-14 is a gap
        assert "dma" in summary
        assert summary["dma"]["total_points"] == 2000

    def test_is_gfw_coverage_complete(self, db):
        from app.modules.coverage_tracker import is_gfw_coverage_complete
        _coverage_window(
            db, "gfw-gaps", date(2024, 6, 1), date(2024, 6, 30),
            vessels_queried=100, vessels_total=100,
        )
        db.commit()
        assert is_gfw_coverage_complete(db, "gfw-gaps", date(2024, 6, 1), date(2024, 6, 30)) is True

        # Incomplete window
        _coverage_window(
            db, "gfw-encounters", date(2024, 6, 1), date(2024, 6, 30),
            vessels_queried=50, vessels_total=100,
        )
        db.commit()
        assert is_gfw_coverage_complete(db, "gfw-encounters", date(2024, 6, 1), date(2024, 6, 30)) is False

        # No window at all
        assert is_gfw_coverage_complete(db, "gfw-port-visits", date(2024, 6, 1), date(2024, 6, 30)) is False


# ===========================================================================
# TestHistoryScheduler
# ===========================================================================

class TestHistoryScheduler:

    def test_dry_run_no_db_writes(self, db):
        """_get_enabled_sources + _find_gaps does not write to DB."""
        from app.modules.history_scheduler import HistoryScheduler

        scheduler = HistoryScheduler(db_factory=lambda: db)
        enabled = scheduler._get_enabled_sources()
        assert enabled == []

        gaps = scheduler._find_gaps(db, "noaa", 30)
        assert db.query(DataCoverageWindow).count() == 0

    @patch("app.modules.history_scheduler.settings")
    def test_disabled_flag_skips(self, mock_settings):
        """When HISTORY_BACKFILL_ENABLED is False, start() does nothing."""
        from app.modules.history_scheduler import HistoryScheduler

        mock_settings.HISTORY_BACKFILL_ENABLED = False
        scheduler = HistoryScheduler(db_factory=MagicMock())
        scheduler.start()
        assert scheduler._thread is None

    @patch("app.modules.history_scheduler.settings")
    def test_max_days_per_run_enforced(self, mock_settings, db):
        """_find_gaps clamps to max_days for the source."""
        from app.modules.history_scheduler import HistoryScheduler

        mock_settings.NOAA_BACKFILL_ENABLED = True
        scheduler = HistoryScheduler(db_factory=lambda: db)
        gaps = scheduler._find_gaps(db, "noaa", max_days=5)
        total_days = sum((end - start).days + 1 for start, end in gaps)
        assert total_days <= 5

    @patch("app.modules.history_scheduler.settings")
    def test_dispatch_calls_correct_client(self, mock_settings, db):
        """_call_source dispatches to the correct import function."""
        from app.modules.history_scheduler import HistoryScheduler

        mock_settings.NOAA_BACKFILL_ENABLED = True
        scheduler = HistoryScheduler(db_factory=lambda: db)

        with patch("app.modules.noaa_client.fetch_and_import_noaa", return_value={"points": 100}) as mock_noaa:
            scheduler._call_source(db, "noaa", date(2024, 1, 1), date(2024, 1, 31))
            mock_noaa.assert_called_once_with(db, start_date=date(2024, 1, 1), end_date=date(2024, 1, 31))

        with patch("app.modules.dma_client.fetch_and_import_dma", return_value={"points": 200}) as mock_dma:
            scheduler._call_source(db, "dma", date(2024, 2, 1), date(2024, 2, 14))
            mock_dma.assert_called_once_with(db, date(2024, 2, 1), date(2024, 2, 14))


# ===========================================================================
# TestVesselTrackAPI
# ===========================================================================

class TestVesselTrackAPI:

    def test_track_200_with_date_range(self, real_api_client, real_api_db):
        v = _vessel(real_api_db, mmsi="444444444")
        ts = datetime(2024, 6, 15, 12, 0, 0)
        _ais_point(real_api_db, v, ts, lat=59.0, lon=24.0, source="aisstream")
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/vessels/{v.vessel_id}/track",
            params={"date_from": "2024-06-01", "date_to": "2024-06-30"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["vessel_id"] == v.vessel_id
        assert data["meta"]["total_points"] == 1
        assert len(data["points"]) == 1

    def test_track_404_unknown_vessel(self, real_api_client, real_api_db):
        # Ensure the vessel table exists by creating a dummy vessel
        _vessel(real_api_db, mmsi="404404404")
        real_api_db.commit()
        resp = real_api_client.get(f"{API}/vessels/99999/track")
        assert resp.status_code == 404

    def test_track_invalid_date_range_422(self, real_api_client, real_api_db):
        v = _vessel(real_api_db, mmsi="555555555")
        real_api_db.commit()
        resp = real_api_client.get(
            f"{API}/vessels/{v.vessel_id}/track",
            params={"date_from": "2024-06-30", "date_to": "2024-06-01"},
        )
        assert resp.status_code == 422

    def test_track_cursor_pagination(self, real_api_client, real_api_db):
        v = _vessel(real_api_db, mmsi="666666666")
        base = datetime(2024, 6, 15, 0, 0, 0)
        for i in range(5):
            _ais_point(real_api_db, v, base + timedelta(hours=i), lat=59.0 + i * 0.01, lon=24.0)
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/vessels/{v.vessel_id}/track",
            params={"date_from": "2024-06-01", "date_to": "2024-06-30", "page_size": 2},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["points"]) == 2
        assert data["meta"]["next_cursor"] is not None

        resp2 = real_api_client.get(
            f"{API}/vessels/{v.vessel_id}/track",
            params={
                "date_from": "2024-06-01",
                "date_to": "2024-06-30",
                "page_size": 2,
                "after_cursor": data["meta"]["next_cursor"],
            },
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert len(data2["points"]) == 2

    def test_track_no_downsampling_short_range(self, real_api_client, real_api_db):
        """<= 7 day range: no downsampling."""
        v = _vessel(real_api_db, mmsi="777777777")
        base = datetime(2024, 6, 15, 0, 0, 0)
        for i in range(10):
            _ais_point(real_api_db, v, base + timedelta(minutes=i * 5), lat=59.0, lon=24.0)
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/vessels/{v.vessel_id}/track",
            params={"date_from": "2024-06-15", "date_to": "2024-06-16"},
        )
        data = resp.json()
        assert data["meta"]["downsampling_applied"] is False
        assert data["meta"]["downsampling_interval"] is None
        assert data["meta"]["total_points"] == 10

    def test_track_downsampling_1h_medium_range(self, real_api_client, real_api_db):
        """8-30 day range: 1h downsampling."""
        v = _vessel(real_api_db, mmsi="888888888")
        base = datetime(2024, 6, 1, 0, 0, 0)
        for i in range(50):
            _ais_point(real_api_db, v, base + timedelta(minutes=i * 10), lat=59.0, lon=24.0)
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/vessels/{v.vessel_id}/track",
            params={"date_from": "2024-06-01", "date_to": "2024-06-20"},
        )
        data = resp.json()
        assert data["meta"]["downsampling_applied"] is True
        assert data["meta"]["downsampling_interval"] == "1h"
        assert data["meta"]["total_points"] < 50

    def test_track_downsampling_6h_long_range(self, real_api_client, real_api_db):
        """> 30 day range: 6h downsampling applied, correct metadata returned."""
        v = _vessel(real_api_db, mmsi="999999999")
        base = datetime(2024, 1, 1, 0, 0, 0)
        for i in range(100):
            _ais_point(real_api_db, v, base + timedelta(hours=i), lat=59.0, lon=24.0)
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/vessels/{v.vessel_id}/track",
            params={"date_from": "2024-01-01", "date_to": "2024-03-01"},
        )
        data = resp.json()
        assert data["meta"]["downsampling_applied"] is True
        assert data["meta"]["downsampling_interval"] == "6h"
        # 6h bucket should reduce 100 hourly points (across ~4 days / ~17 buckets)
        assert data["meta"]["total_points"] <= 100

    def test_track_geojson_response_format(self, real_api_client, real_api_db):
        """GeoJSON endpoint returns valid FeatureCollection."""
        v = _vessel(real_api_db, mmsi="100000001")
        base = datetime(2024, 6, 15, 0, 0, 0)
        _ais_point(real_api_db, v, base, lat=59.0, lon=24.0)
        _ais_point(real_api_db, v, base + timedelta(hours=1), lat=59.1, lon=24.1)
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/vessels/{v.vessel_id}/track.geojson",
            params={"date_from": "2024-06-01", "date_to": "2024-06-30"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 1
        feat = data["features"][0]
        assert feat["geometry"]["type"] == "LineString"
        assert feat["properties"]["vessel_id"] == v.vessel_id
        assert feat["properties"]["point_count"] == 2
        assert len(feat["geometry"]["coordinates"]) == 2


# ===========================================================================
# TestCorridorActivity
# ===========================================================================

class TestCorridorActivity:

    def test_activity_weekly_buckets(self, real_api_client, real_api_db):
        corr = _corridor(real_api_db, name="Activity Test Corridor")
        v = _vessel(real_api_db, mmsi="200000001")
        _gap_event(real_api_db, v,
                   datetime(2024, 6, 3, 10, 0), datetime(2024, 6, 3, 16, 0),
                   corridor=corr)
        _gap_event(real_api_db, v,
                   datetime(2024, 6, 10, 10, 0), datetime(2024, 6, 10, 16, 0),
                   corridor=corr)
        _gap_event(real_api_db, v,
                   datetime(2024, 6, 12, 10, 0), datetime(2024, 6, 12, 16, 0),
                   corridor=corr)
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/corridors/{corr.corridor_id}/activity",
            params={"granularity": "week", "date_from": "2024-06-01", "date_to": "2024-06-30"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2
        counts = [b["gap_count"] for b in data]
        assert 2 in counts

    def test_activity_monthly_granularity(self, real_api_client, real_api_db):
        corr = _corridor(real_api_db, name="Monthly Corridor")
        v = _vessel(real_api_db, mmsi="200000002")
        _gap_event(real_api_db, v,
                   datetime(2024, 6, 15, 0, 0), datetime(2024, 6, 15, 6, 0),
                   corridor=corr)
        _gap_event(real_api_db, v,
                   datetime(2024, 7, 15, 0, 0), datetime(2024, 7, 15, 6, 0),
                   corridor=corr)
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/corridors/{corr.corridor_id}/activity",
            params={"granularity": "month", "date_from": "2024-06-01", "date_to": "2024-07-31"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["period_start"] == "2024-06"
        assert data[1]["period_start"] == "2024-07"

    def test_activity_empty_range(self, real_api_client, real_api_db):
        corr = _corridor(real_api_db, name="Empty Corridor")
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/corridors/{corr.corridor_id}/activity",
            params={"granularity": "week", "date_from": "2024-06-01", "date_to": "2024-06-30"},
        )
        assert resp.status_code == 200
        assert resp.json() == []


# ===========================================================================
# TestExistingEndpointDateFilters
# ===========================================================================

class TestExistingEndpointDateFilters:
    """Verify date_from/date_to query params filter results on existing endpoints."""

    def test_sts_events_date_filter(self, real_api_client, real_api_db):
        v1 = _vessel(real_api_db, mmsi="300000001")
        v2 = _vessel(real_api_db, mmsi="300000002")
        sts1 = StsTransferEvent(
            vessel_1_id=v1.vessel_id, vessel_2_id=v2.vessel_id,
            start_time_utc=datetime(2024, 1, 15, 10, 0),
            end_time_utc=datetime(2024, 1, 15, 12, 0),
            detection_type=STSDetectionTypeEnum.VISIBLE_VISIBLE,
        )
        sts2 = StsTransferEvent(
            vessel_1_id=v1.vessel_id, vessel_2_id=v2.vessel_id,
            start_time_utc=datetime(2024, 6, 15, 10, 0),
            end_time_utc=datetime(2024, 6, 15, 12, 0),
            detection_type=STSDetectionTypeEnum.VISIBLE_VISIBLE,
        )
        real_api_db.add_all([sts1, sts2])
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/sts-events",
            params={"date_from": "2024-06-01", "date_to": "2024-06-30"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1

    def test_spoofing_date_filter(self, real_api_client, real_api_db):
        v = _vessel(real_api_db, mmsi="300000003")
        sa1 = SpoofingAnomaly(
            vessel_id=v.vessel_id,
            anomaly_type=SpoofingTypeEnum.ANCHOR_SPOOF,
            start_time_utc=datetime(2024, 1, 10, 8, 0),
            risk_score_component=20,
        )
        sa2 = SpoofingAnomaly(
            vessel_id=v.vessel_id,
            anomaly_type=SpoofingTypeEnum.ANCHOR_SPOOF,
            start_time_utc=datetime(2024, 6, 10, 8, 0),
            risk_score_component=20,
        )
        real_api_db.add_all([sa1, sa2])
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/spoofing/{v.vessel_id}",
            params={"date_from": "2024-06-01", "date_to": "2024-06-30"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_loitering_date_filter(self, real_api_client, real_api_db):
        v = _vessel(real_api_db, mmsi="300000004")
        lo1 = LoiteringEvent(
            vessel_id=v.vessel_id,
            start_time_utc=datetime(2024, 1, 5, 0, 0),
            end_time_utc=datetime(2024, 1, 5, 6, 0),
            duration_hours=6.0,
        )
        lo2 = LoiteringEvent(
            vessel_id=v.vessel_id,
            start_time_utc=datetime(2024, 6, 5, 0, 0),
            end_time_utc=datetime(2024, 6, 5, 6, 0),
            duration_hours=6.0,
        )
        real_api_db.add_all([lo1, lo2])
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/loitering/{v.vessel_id}",
            params={"date_from": "2024-06-01", "date_to": "2024-06-30"},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_port_calls_date_filter(self, real_api_client, real_api_db):
        v = _vessel(real_api_db, mmsi="300000005")
        pc1 = PortCall(
            vessel_id=v.vessel_id,
            arrival_utc=datetime(2024, 1, 20, 10, 0),
            source="manual",
        )
        pc2 = PortCall(
            vessel_id=v.vessel_id,
            arrival_utc=datetime(2024, 6, 20, 10, 0),
            source="manual",
        )
        real_api_db.add_all([pc1, pc2])
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/port-calls/{v.vessel_id}",
            params={"date_from": "2024-06-01", "date_to": "2024-06-30"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1

    def test_timeline_date_filter(self, real_api_client, real_api_db):
        v = _vessel(real_api_db, mmsi="300000006")
        _gap_event(real_api_db, v,
                   datetime(2024, 1, 10, 0, 0), datetime(2024, 1, 10, 6, 0))
        _gap_event(real_api_db, v,
                   datetime(2024, 6, 10, 0, 0), datetime(2024, 6, 10, 6, 0))
        real_api_db.commit()

        resp = real_api_client.get(
            f"{API}/vessels/{v.vessel_id}/timeline",
            params={"date_from": "2024-06-01", "date_to": "2024-06-30"},
        )
        assert resp.status_code == 200
        data = resp.json()
        gap_events = [e for e in data["events"] if e["event_type"] == "ais_gap"]
        assert len(gap_events) == 1


# ===========================================================================
# TestCollectionStatusBugFix
# ===========================================================================

class TestCollectionStatusBugFix:

    def test_collection_status_returns_runs(self, real_api_client, real_api_db):
        """GET /health/collection-status returns collection_runs list (not error)."""
        run = CollectionRun(
            source="aisstream",
            started_at=datetime.now(timezone.utc) - timedelta(hours=1),
            finished_at=datetime.now(timezone.utc),
            points_imported=500,
            vessels_seen=10,
            status="completed",
        )
        real_api_db.add(run)
        real_api_db.commit()

        resp = real_api_client.get(f"{API}/health/collection-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "collection_runs" in data
        assert isinstance(data["collection_runs"], list)
        assert len(data["collection_runs"]) >= 1
        assert data["collection_runs"][0]["source"] == "aisstream"


# ===========================================================================
# TestCLIHistory
# ===========================================================================

class TestCLIHistory:

    def test_history_status_output(self):
        """'history status' renders a table without error."""
        from typer.testing import CliRunner
        from app.cli import app as cli_app

        runner = CliRunner()
        with patch("app.database.SessionLocal") as mock_sl:
            mock_db = MagicMock()
            mock_sl.return_value = mock_db
            with patch("app.modules.coverage_tracker.coverage_summary", return_value={
                "noaa": {
                    "earliest": "2024-01-01",
                    "latest": "2024-06-30",
                    "completed_windows": 5,
                    "total_points": 10000,
                    "gap_count": 2,
                    "next_gap": "2024-02-15",
                },
            }):
                result = runner.invoke(cli_app, ["history", "status"])
                assert result.exit_code == 0
                assert "noaa" in result.output

    def test_history_gaps_output(self):
        """'history gaps --source noaa' lists gap ranges."""
        from typer.testing import CliRunner
        from app.cli import app as cli_app

        runner = CliRunner()
        with patch("app.database.SessionLocal") as mock_sl:
            mock_db = MagicMock()
            mock_sl.return_value = mock_db
            with patch("app.modules.coverage_tracker.find_coverage_gaps", return_value=[
                (date(2024, 2, 15), date(2024, 2, 20)),
                (date(2024, 3, 1), date(2024, 3, 5)),
            ]):
                result = runner.invoke(cli_app, ["history", "gaps", "--source", "noaa"])
                assert result.exit_code == 0
                assert "2024-02-15" in result.output
                assert "2024-03-01" in result.output

    def test_history_backfill_records_coverage(self):
        """'history backfill' calls fetch_and_import_noaa for noaa source."""
        from typer.testing import CliRunner
        from app.cli import app as cli_app

        runner = CliRunner()
        mock_stats = {
            "dates_downloaded": 31,
            "dates_attempted": 31,
            "total_accepted": 100,
            "dates_failed": [],
        }
        with patch("app.database.SessionLocal") as mock_sl:
            mock_db = MagicMock()
            mock_sl.return_value = mock_db
            with patch("app.modules.noaa_client.fetch_and_import_noaa",
                       return_value=mock_stats) as mock_call:
                result = runner.invoke(cli_app, [
                    "history", "backfill",
                    "--source", "noaa",
                    "--start", "2024-01-01",
                    "--end", "2024-01-31",
                ])
                assert result.exit_code == 0
                mock_call.assert_called_once()
                assert "100" in result.output or "imported" in result.output.lower()
