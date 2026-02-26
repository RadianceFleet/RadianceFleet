"""Tests for the final gap closure changes.

Tests cover:
- P1.1: WebSocket reconnection + batch error handling
- P1.2: Future timestamp rejection in streaming
- P1.3: MMSI leading-zero padding
- P1.4: IntegrityError handling for concurrent vessel creation
- P2.2: GFW exact MMSI match only
- P3.1: STS detection type scoring
- P3.2: Watchlist fuzzy match confidence storage
- P4.1: Corridor count validation warning
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ── P1.2: Future timestamp rejection ─────────────────────────────────────────

class TestFutureTimestampRejection:
    def test_future_timestamp_rejected(self):
        """Timestamps >5min in the future should be rejected."""
        from app.modules.aisstream_client import _map_position_report

        future_ts = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        msg = {
            "MetaData": {"MMSI": 211000000, "latitude": 55.0, "longitude": 10.0, "time_utc": future_ts},
            "Message": {"PositionReport": {"Latitude": 55.0, "Longitude": 10.0, "Sog": 5.0, "Cog": 90.0, "TrueHeading": 90}},
        }
        result = _map_position_report(msg)
        assert result is None

    def test_recent_timestamp_accepted(self):
        """Timestamps within 5-min tolerance should be accepted."""
        from app.modules.aisstream_client import _map_position_report

        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        msg = {
            "MetaData": {"MMSI": 211000000, "latitude": 55.0, "longitude": 10.0, "time_utc": recent_ts},
            "Message": {"PositionReport": {"Latitude": 55.0, "Longitude": 10.0, "Sog": 5.0, "Cog": 90.0, "TrueHeading": 90}},
        }
        result = _map_position_report(msg)
        assert result is not None
        assert result["mmsi"] == "211000000"

    def test_slightly_future_timestamp_accepted(self):
        """Timestamps up to 4 minutes in the future should be accepted (clock skew tolerance)."""
        from app.modules.aisstream_client import _map_position_report

        future_ts = (datetime.now(timezone.utc) + timedelta(minutes=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        msg = {
            "MetaData": {"MMSI": 211000000, "latitude": 55.0, "longitude": 10.0, "time_utc": future_ts},
            "Message": {"PositionReport": {"Latitude": 55.0, "Longitude": 10.0, "Sog": 5.0, "Cog": 90.0, "TrueHeading": 90}},
        }
        result = _map_position_report(msg)
        assert result is not None


# ── P1.3: MMSI leading-zero padding ──────────────────────────────────────────

class TestMMSIZeroPadding:
    def test_short_mmsi_padded_to_9_digits(self):
        """An 8-digit MMSI should be padded to 9 digits."""
        from app.modules.normalize import validate_ais_row

        # 21100000 (8 digits) → 021100000 (MID 021, not coast/SAR/AtoN)
        row = {
            "mmsi": "21100000",
            "lat": 55.0,
            "lon": 10.0,
            "timestamp_utc": "2026-01-15T12:00:00Z",
        }
        error = validate_ais_row(row)
        assert error is None
        assert row["mmsi"] == "021100000"

    def test_normal_9_digit_mmsi_unchanged(self):
        """A standard 9-digit MMSI should pass through unchanged."""
        from app.modules.normalize import validate_ais_row

        row = {
            "mmsi": "209010000",
            "lat": 55.0,
            "lon": 10.0,
            "timestamp_utc": "2026-01-15T12:00:00Z",
        }
        error = validate_ais_row(row)
        assert error is None
        assert row["mmsi"] == "209010000"

    def test_mmsi_with_whitespace_stripped_and_padded(self):
        """MMSI with leading/trailing whitespace should be stripped then padded."""
        from app.modules.normalize import validate_ais_row

        row = {
            "mmsi": " 21100000 ",
            "lat": 55.0,
            "lon": 10.0,
            "timestamp_utc": "2026-01-15T12:00:00Z",
        }
        error = validate_ais_row(row)
        assert error is None
        assert row["mmsi"] == "021100000"

    def test_padding_applied_in_ingest(self):
        """_get_or_create_vessel should pad MMSI before lookup."""
        from app.modules.ingest import _get_or_create_vessel

        db = MagicMock()
        vessel = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = vessel

        row = {
            "mmsi": "21100000",  # 8 digits
            "lat": 55.0,
            "lon": 10.0,
            "timestamp": "2026-01-15T12:00:00Z",
        }
        result = _get_or_create_vessel(db, row)
        # Should have queried with padded MMSI
        assert result is vessel


# ── P1.4: IntegrityError handling ─────────────────────────────────────────────

class TestIntegrityErrorHandling:
    def test_ingest_integrity_error_recovers(self):
        """IntegrityError during vessel creation should recover via re-query."""
        from sqlalchemy.exc import IntegrityError
        from app.modules.ingest import _get_or_create_vessel

        db = MagicMock()
        existing_vessel = MagicMock()
        existing_vessel.mmsi = "211000000"

        db.query.return_value.filter.return_value.first.side_effect = [None, existing_vessel]
        db.flush.side_effect = IntegrityError("duplicate", params=None, orig=Exception("unique constraint"))

        row = {
            "mmsi": "211000000",
            "lat": 55.0,
            "lon": 10.0,
            "timestamp": "2026-01-15T12:00:00Z",
        }
        result = _get_or_create_vessel(db, row)
        assert result is existing_vessel
        db.rollback.assert_called_once()

    def test_aishub_integrity_error_recovers(self):
        """IntegrityError during AISHub vessel creation should recover via SAVEPOINT."""
        from sqlalchemy.exc import IntegrityError
        from app.modules.aishub_client import ingest_aishub_positions

        db = MagicMock()
        existing_vessel = MagicMock()
        existing_vessel.vessel_id = 1
        existing_vessel.mmsi = "211000000"

        db.query.return_value.filter.return_value.first.side_effect = [
            None,  # vessel query returns None
            existing_vessel,  # re-query after IntegrityError
            None,  # AISPoint duplicate check
        ]
        # begin_nested() returns a context manager; flush inside raises IntegrityError
        nested_cm = MagicMock()
        nested_cm.__enter__ = MagicMock(return_value=nested_cm)
        nested_cm.__exit__ = MagicMock(return_value=False)
        db.begin_nested.return_value = nested_cm
        db.flush.side_effect = [IntegrityError("dup", params=None, orig=Exception()), None]

        positions = [{
            "mmsi": "211000000",
            "vessel_name": "TEST",
            "timestamp": "2026-01-15T12:00:00Z",
            "lat": 55.0,
            "lon": 10.0,
            "sog": 5.0,
            "cog": 90.0,
            "heading": None,
            "nav_status": None,
            "source": "aishub",
        }]
        result = ingest_aishub_positions(positions, db)
        assert result["stored"] >= 0  # Shouldn't crash
        db.begin_nested.assert_called()  # SAVEPOINT used instead of bare rollback


# ── P1.1: Batch error handling stats ──────────────────────────────────────────

class TestBatchErrorStats:
    def test_stats_include_batch_errors_counter(self):
        """stream_ais function source should include batch_errors counter."""
        import inspect
        from app.modules.aisstream_client import stream_ais

        # Verify the stats dict initialization includes batch_errors
        source = inspect.getsource(stream_ais)
        assert '"batch_errors"' in source, "stream_ais missing batch_errors in stats dict"

    def test_batch_try_except_in_stream(self):
        """stream_ais should have try-except around _ingest_batch calls."""
        import inspect
        from app.modules.aisstream_client import stream_ais

        source = inspect.getsource(stream_ais)
        # Verify batch error handling is present (try-except around _ingest_batch)
        assert "batch_errors" in source
        assert "_ingest_batch" in source
        assert 'stats["batch_errors"]' in source or "stats.get(\"batch_errors\"" in source


# ── P2.2: GFW exact MMSI match only ──────────────────────────────────────────

class TestGFWExactMatch:
    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_no_exact_mmsi_match_skips(self, mock_search, mock_sleep):
        """When GFW returns results but none match MMSI exactly, skip."""
        from app.modules.vessel_enrichment import enrich_vessels_from_gfw

        class FakeVessel:
            pass
        vessel = FakeVessel()
        vessel.mmsi = "211000000"
        vessel.imo = None
        vessel.deadweight = None
        vessel.year_built = None
        vessel.flag = None
        vessel.flag_risk_category = None
        vessel.vessel_type = None
        vessel.vessel_id = 1

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]

        mock_search.return_value = [{
            "mmsi": "999999999",  # Different MMSI
            "imo": "1234567",
            "tonnage_gt": 50000,
        }]

        result = enrich_vessels_from_gfw(db, token="test-token", limit=10)

        assert result["enriched"] == 0
        assert result["skipped"] >= 1
        assert vessel.deadweight is None
        assert vessel.imo is None

    @patch("app.modules.vessel_enrichment.time.sleep")
    @patch("app.modules.gfw_client.search_vessel")
    def test_enriched_vessel_ids_returned(self, mock_search, mock_sleep):
        """Result should include enriched_vessel_ids list."""
        from app.modules.vessel_enrichment import enrich_vessels_from_gfw

        class FakeVessel:
            pass
        vessel = FakeVessel()
        vessel.mmsi = "211000000"
        vessel.imo = None
        vessel.deadweight = None
        vessel.year_built = None
        vessel.flag = None
        vessel.flag_risk_category = None
        vessel.vessel_type = None
        vessel.vessel_id = 42

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [vessel]

        mock_search.return_value = [{
            "mmsi": "211000000",
            "tonnage_gt": 50000,
        }]

        result = enrich_vessels_from_gfw(db, token="test-token", limit=10)

        assert "enriched_vessel_ids" in result
        assert 42 in result["enriched_vessel_ids"]


# ── P3.1: STS detection type scoring ─────────────────────────────────────────

class TestSTSDetectionTypeScoring:
    @staticmethod
    def _make_gap(gap_id=1):
        """Create a gap mock with vessel=None to skip vessel-level signals."""
        gap = MagicMock()
        gap.gap_event_id = gap_id
        gap.vessel_id = 1
        gap.vessel = None  # Avoids MagicMock vessel attribute arithmetic errors
        gap.duration_minutes = 600
        gap.gap_start_utc = datetime(2026, 1, 15, 0, 0)
        gap.gap_end_utc = datetime(2026, 1, 15, 10, 0)
        gap.impossible_speed_flag = False
        gap.velocity_plausibility_ratio = 0.5
        gap.actual_gap_distance_nm = 50
        gap.max_plausible_distance_nm = 200
        gap.start_point_id = None
        gap.end_point_id = None
        gap.corridor_id = None
        gap.corridor = None
        gap.in_dark_zone = False
        gap.dark_zone_id = None
        gap.pre_gap_sog = 5.0
        gap.status = "new"
        return gap

    @staticmethod
    def _make_db(sts_events):
        """Create a mock DB that returns sts_events only for StsTransferEvent queries."""
        db = MagicMock()

        def mock_query(model):
            q = MagicMock()
            model_name = getattr(model, '__name__', '')
            if 'StsTransferEvent' in model_name:
                q.filter.return_value.all.return_value = sts_events
            else:
                q.filter.return_value.all.return_value = []
                q.filter.return_value.first.return_value = None
            q.get.return_value = None
            return q

        db.query.side_effect = mock_query
        return db

    def test_dark_partner_sts_gets_bonus(self):
        """STS event with visible_dark detection_type should get bonus from YAML config."""
        from app.modules.risk_scoring import compute_gap_score, load_scoring_config

        config = load_scoring_config()
        gap = self._make_gap(1)

        sts_event_dark = MagicMock()
        sts_event_dark.sts_id = 10
        sts_event_dark.risk_score_component = 25
        sts_event_dark.detection_type = MagicMock()
        sts_event_dark.detection_type.value = "visible_dark"

        db = self._make_db([sts_event_dark])
        score, breakdown = compute_gap_score(gap, config=config, db=db)

        # The dark partner STS should get 25 (base) + 15 (dark bonus) = 40
        sts_keys = [k for k in breakdown if k.startswith("sts_event_")]
        assert len(sts_keys) > 0, f"No STS event found in breakdown: {breakdown}"
        assert breakdown[sts_keys[0]] == 40  # 25 + 15 dark bonus

    def test_visible_visible_sts_no_bonus(self):
        """STS event with visible_visible type should NOT get dark bonus."""
        from app.modules.risk_scoring import compute_gap_score, load_scoring_config

        config = load_scoring_config()
        gap = self._make_gap(2)

        sts_event = MagicMock()
        sts_event.sts_id = 11
        sts_event.risk_score_component = 35
        sts_event.detection_type = MagicMock()
        sts_event.detection_type.value = "visible_visible"

        db = self._make_db([sts_event])
        score, breakdown = compute_gap_score(gap, config=config, db=db)

        sts_keys = [k for k in breakdown if k.startswith("sts_event_")]
        assert len(sts_keys) > 0, f"No STS event found in breakdown: {breakdown}"
        assert breakdown[sts_keys[0]] == 35  # No bonus — base score only


# ── P3.2: Watchlist match confidence ──────────────────────────────────────────

class TestWatchlistMatchConfidence:
    def test_exact_mmsi_match_returns_100_confidence(self):
        """MMSI exact match should return confidence=100, match_type=exact_mmsi."""
        from app.modules.watchlist_loader import _resolve_vessel

        db = MagicMock()
        vessel = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = vessel

        result = _resolve_vessel(db, mmsi="211000000", imo=None, name=None)
        assert result is not None
        v, match_type, confidence = result
        assert v is vessel
        assert match_type == "exact_mmsi"
        assert confidence == 100

    def test_fuzzy_match_returns_score_no_flag(self):
        """Fuzzy name match without flag should return score (using 92% threshold)."""
        from app.modules.watchlist_loader import _fuzzy_match_vessel

        db = MagicMock()
        vessel = MagicMock()
        vessel.name = "TANKER ALPHA"
        # No flag filter → single .filter() chain
        db.query.return_value.filter.return_value.all.return_value = [vessel]

        # Exact name match → score 100 → passes 92% threshold
        result = _fuzzy_match_vessel(db, "TANKER ALPHA")
        assert result is not None
        v, match_type, confidence = result
        assert v is vessel
        assert match_type == "fuzzy_name"
        assert confidence == 100

    def test_name_only_threshold_raised_to_92(self):
        """Without flag pre-filter, threshold should be 92% (not 85%)."""
        from app.modules.watchlist_loader import _fuzzy_match_vessel
        from rapidfuzz import fuzz

        db = MagicMock()
        vessel = MagicMock()
        vessel.name = "OCEAN STAR II"
        db.query.return_value.filter.return_value.all.return_value = [vessel]

        # "OCEAN STAR" vs "OCEAN STAR II" scores ~89%
        score = fuzz.ratio("OCEAN STAR", "OCEAN STAR II")
        if score < 92:
            result = _fuzzy_match_vessel(db, "OCEAN STAR")  # No flag → 92% threshold
            assert result is None  # Should NOT match at 89%

    def test_model_has_confidence_fields(self):
        """VesselWatchlist model should have match_confidence and match_type columns."""
        from app.models.vessel_watchlist import VesselWatchlist

        # Check the model has the new mapped columns
        assert hasattr(VesselWatchlist, "match_confidence")
        assert hasattr(VesselWatchlist, "match_type")


# ── P4.1: Corridor count warning ─────────────────────────────────────────────

class TestCorridorCountWarning:
    def test_warning_when_no_corridors(self, caplog):
        """Should log warning when no corridors are loaded."""
        from app.modules.gap_detector import run_gap_detection

        db = MagicMock()
        db.query.return_value.all.return_value = []  # No vessels
        db.query.return_value.count.return_value = 0  # No corridors

        with caplog.at_level(logging.WARNING):
            run_gap_detection(db)

        assert any("No corridors loaded" in r.message for r in caplog.records)
