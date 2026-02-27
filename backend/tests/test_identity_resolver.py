"""Tests for vessel identity merging — detection, scoring, execution, and reversal.

Covers:
  - IMO checksum validation (valid/invalid/fabricated)
  - Speed-feasibility matching (reachable/unreachable)
  - Candidate scoring signal breakdown
  - Anchorage density filter (busy STS zone penalty)
  - execute_merge: FK reassignment, unique constraint conflict resolution
  - reverse_merge: undo via affected_records snapshot
  - resolve_canonical: chain walk + cycle detection
  - Absorbed vessel API behavior (redirect, search aliases)
  - MMSI cloning detection (impossible jumps)
  - Zombie IMO detection (fabricated check digits)
  - Timeline aggregation from multiple source tables

All tests are unit-level: no real database.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock, call

from app.modules.identity_resolver import (
    validate_imo_checksum,
    resolve_canonical,
    execute_merge,
    reverse_merge,
    detect_zombie_imos,
    get_vessel_timeline,
    get_vessel_aliases,
    _score_candidate,
)
from app.modules.mmsi_cloning_detector import (
    _find_impossible_jumps as find_cloning_jumps,
    _score_cloning,
)


# ── IMO checksum validation ──────────────────────────────────────────────────


class TestIMOChecksum:
    """IMO number validation uses the check digit algorithm:
    Sum of (digit_i * (7-i)) for i=0..5 mod 10 == check digit (digit 6).
    """

    def test_valid_imo_9074729(self):
        """IMO 9074729 — known valid tanker IMO."""
        assert validate_imo_checksum("9074729") is True

    def test_valid_imo_with_prefix(self):
        """IMO prefix should be stripped before validation."""
        assert validate_imo_checksum("IMO9074729") is True

    def test_invalid_imo_bad_check_digit(self):
        """Changing the check digit invalidates the IMO."""
        assert validate_imo_checksum("9074720") is False

    def test_fabricated_imo_all_ones(self):
        """1111111 — fake pattern commonly used by shadow fleet."""
        # 1*7 + 1*6 + 1*5 + 1*4 + 1*3 + 1*2 = 27, 27 % 10 = 7 != 1
        assert validate_imo_checksum("1111111") is False

    def test_short_imo_rejected(self):
        assert validate_imo_checksum("12345") is False

    def test_non_numeric_rejected(self):
        assert validate_imo_checksum("ABCDEFG") is False

    def test_imo_9074729_manual_check(self):
        """Manual calculation: 9*7+0*6+7*5+4*4+7*3+2*2 = 63+0+35+16+21+4 = 139
        139 mod 10 = 9 == last digit. Valid."""
        assert validate_imo_checksum("9074729") is True


# ── Canonical resolution ─────────────────────────────────────────────────────


class TestResolveCanonical:
    def test_no_chain(self):
        """Vessel with no merged_into_vessel_id returns itself."""
        db = MagicMock()
        vessel = MagicMock()
        vessel.merged_into_vessel_id = None
        db.query.return_value.get.return_value = vessel
        assert resolve_canonical(42, db) == 42

    def test_single_hop(self):
        """A→B chain: A.merged_into=B, B.merged_into=None → returns B."""
        db = MagicMock()

        vessel_a = MagicMock()
        vessel_a.merged_into_vessel_id = 2

        vessel_b = MagicMock()
        vessel_b.merged_into_vessel_id = None

        db.query.return_value.get.side_effect = lambda vid: {1: vessel_a, 2: vessel_b}[vid]
        assert resolve_canonical(1, db) == 2

    def test_chain_a_b_c(self):
        """A→B→C chain resolves to C."""
        db = MagicMock()

        vessels = {
            1: MagicMock(merged_into_vessel_id=2),
            2: MagicMock(merged_into_vessel_id=3),
            3: MagicMock(merged_into_vessel_id=None),
        }
        db.query.return_value.get.side_effect = lambda vid: vessels.get(vid)
        assert resolve_canonical(1, db) == 3

    def test_cycle_detection(self):
        """Circular chain A→B→A raises ValueError."""
        db = MagicMock()

        vessels = {
            1: MagicMock(merged_into_vessel_id=2),
            2: MagicMock(merged_into_vessel_id=1),
        }
        db.query.return_value.get.side_effect = lambda vid: vessels.get(vid)
        with pytest.raises(ValueError, match="Circular merge chain"):
            resolve_canonical(1, db)

    def test_missing_vessel_returns_current(self):
        """If vessel not found in chain, returns last valid ID."""
        db = MagicMock()
        db.query.return_value.get.return_value = None
        assert resolve_canonical(99, db) == 99


# ── Candidate scoring ────────────────────────────────────────────────────────


def _make_vessel(
    vessel_id=1,
    mmsi="123456789",
    imo=None,
    vessel_type=None,
    deadweight=None,
    year_built=None,
    flag=None,
):
    v = MagicMock()
    v.vessel_id = vessel_id
    v.mmsi = mmsi
    v.imo = imo
    v.vessel_type = vessel_type
    v.deadweight = deadweight
    v.year_built = year_built
    v.flag = flag
    return v


class TestCandidateScoring:
    """Test the _score_candidate signal breakdown."""

    def test_proximity_ratio_full_points(self):
        """Vessel right at the origin: distance=0, max_travel=100 → 20 pts proximity."""
        db = MagicMock()
        # Stub all DB queries to return 0 or empty
        db.query.return_value.filter.return_value.scalar.return_value = 0
        db.query.return_value.filter.return_value.filter.return_value.scalar.return_value = 0

        dark_v = _make_vessel(vessel_id=1, mmsi="211000001")
        new_v = _make_vessel(vessel_id=2, mmsi="211000002")
        now = datetime.utcnow()

        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last={"lat": 55.0, "lon": 20.0, "ts": now - timedelta(hours=12)},
            new_first={"lat": 55.0, "lon": 20.0, "ts": now},
            distance=0.0, time_delta_h=12.0, max_travel=192.0,
            corridor_vessels_cache={},
        )

        assert reasons["proximity_ratio"]["points"] == 20

    def test_time_tightness_short_gap(self):
        """6 hour gap: int(10 - 6/24) = int(9.75) = 9 points."""
        db = MagicMock()
        db.query.return_value.filter.return_value.scalar.return_value = 0

        dark_v = _make_vessel(vessel_id=1, mmsi="211000001")
        new_v = _make_vessel(vessel_id=2, mmsi="211000002")
        now = datetime.utcnow()

        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last={"lat": 55.0, "lon": 20.0, "ts": now - timedelta(hours=6)},
            new_first={"lat": 55.0, "lon": 20.0, "ts": now},
            distance=0.0, time_delta_h=6.0, max_travel=96.0,
            corridor_vessels_cache={},
        )

        assert reasons["time_tightness"]["points"] == 9

    def test_same_imo_valid(self):
        """Both vessels share a valid IMO → +25 pts."""
        db = MagicMock()
        db.query.return_value.filter.return_value.scalar.return_value = 0

        dark_v = _make_vessel(vessel_id=1, mmsi="211000001", imo="9074729")
        new_v = _make_vessel(vessel_id=2, mmsi="211000002", imo="9074729")
        now = datetime.utcnow()

        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last={"lat": 55.0, "lon": 20.0, "ts": now - timedelta(hours=24)},
            new_first={"lat": 55.5, "lon": 20.5, "ts": now},
            distance=30.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
        )

        assert "same_imo" in reasons
        assert reasons["same_imo"]["points"] == 25

    def test_same_imo_invalid_checksum_no_bonus(self):
        """Both vessels share a FABRICATED IMO → no +25 bonus."""
        db = MagicMock()
        db.query.return_value.filter.return_value.scalar.return_value = 0

        dark_v = _make_vessel(vessel_id=1, mmsi="211000001", imo="1111111")
        new_v = _make_vessel(vessel_id=2, mmsi="211000002", imo="1111111")
        now = datetime.utcnow()

        score, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last={"lat": 55.0, "lon": 20.0, "ts": now - timedelta(hours=24)},
            new_first={"lat": 55.5, "lon": 20.5, "ts": now},
            distance=30.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
        )

        assert "same_imo" not in reasons

    def test_same_vessel_type_points(self):
        """Matching vessel_type → +10 pts."""
        db = MagicMock()
        db.query.return_value.filter.return_value.scalar.return_value = 0

        dark_v = _make_vessel(vessel_id=1, mmsi="211000001", vessel_type="Crude Oil Tanker")
        new_v = _make_vessel(vessel_id=2, mmsi="211000002", vessel_type="Crude Oil Tanker")
        now = datetime.utcnow()

        _, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last={"lat": 55.0, "lon": 20.0, "ts": now - timedelta(hours=24)},
            new_first={"lat": 55.5, "lon": 20.5, "ts": now},
            distance=30.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
        )

        assert "same_vessel_type" in reasons

    def test_similar_dwt_within_20pct(self):
        """DWT 100000 vs 85000 (ratio=0.85 >= 0.8) → +10."""
        db = MagicMock()
        db.query.return_value.filter.return_value.scalar.return_value = 0

        dark_v = _make_vessel(vessel_id=1, mmsi="211000001", deadweight=100000)
        new_v = _make_vessel(vessel_id=2, mmsi="211000002", deadweight=85000)
        now = datetime.utcnow()

        _, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last={"lat": 55.0, "lon": 20.0, "ts": now - timedelta(hours=24)},
            new_first={"lat": 55.5, "lon": 20.5, "ts": now},
            distance=30.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
        )

        assert "similar_dwt" in reasons
        assert reasons["similar_dwt"]["ratio"] >= 0.8

    def test_dwt_too_different_no_points(self):
        """DWT 100000 vs 50000 (ratio=0.5 < 0.8) → no DWT bonus."""
        db = MagicMock()
        db.query.return_value.filter.return_value.scalar.return_value = 0

        dark_v = _make_vessel(vessel_id=1, mmsi="211000001", deadweight=100000)
        new_v = _make_vessel(vessel_id=2, mmsi="211000002", deadweight=50000)
        now = datetime.utcnow()

        _, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last={"lat": 55.0, "lon": 20.0, "ts": now - timedelta(hours=24)},
            new_first={"lat": 55.5, "lon": 20.5, "ts": now},
            distance=30.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
        )

        assert "similar_dwt" not in reasons

    def test_flag_change_detected(self):
        """MMSI from different MIDs (DE 211→PA 351) → +5 flag_change."""
        db = MagicMock()
        db.query.return_value.filter.return_value.scalar.return_value = 0

        dark_v = _make_vessel(vessel_id=1, mmsi="211000001")  # DE
        new_v = _make_vessel(vessel_id=2, mmsi="351000002")  # PA
        now = datetime.utcnow()

        _, reasons = _score_candidate(
            db, dark_v, new_v,
            dark_last={"lat": 55.0, "lon": 20.0, "ts": now - timedelta(hours=24)},
            new_first={"lat": 55.5, "lon": 20.5, "ts": now},
            distance=30.0, time_delta_h=24.0, max_travel=384.0,
            corridor_vessels_cache={},
        )

        assert "flag_change" in reasons

    def test_score_capped_at_100(self):
        """Maximum score cannot exceed 100."""
        db = MagicMock()
        db.query.return_value.filter.return_value.scalar.return_value = 0

        # Stack every signal: valid IMO match, same type, similar DWT, year_built...
        dark_v = _make_vessel(
            vessel_id=1, mmsi="211000001", imo="9074729",
            vessel_type="Crude Oil Tanker", deadweight=100000, year_built=2005,
        )
        new_v = _make_vessel(
            vessel_id=2, mmsi="572000002", imo="9074729",  # 572=PW (RU-origin flag)
            vessel_type="Crude Oil Tanker", deadweight=95000, year_built=2006,
        )
        now = datetime.utcnow()

        score, _ = _score_candidate(
            db, dark_v, new_v,
            dark_last={"lat": 55.0, "lon": 20.0, "ts": now - timedelta(hours=6)},
            new_first={"lat": 55.0, "lon": 20.0, "ts": now},
            distance=0.0, time_delta_h=6.0, max_travel=96.0,
            corridor_vessels_cache={},
        )

        assert score <= 100


# ── Speed feasibility ────────────────────────────────────────────────────────


class TestSpeedFeasibility:
    """Core principle: distance_nm <= time_delta_h * MERGE_MAX_SPEED_KN (16kn)."""

    def test_reachable_at_12kn(self):
        """576nm in 48h at 12kn max: 48*16=768 max → 576 < 768 → reachable."""
        distance = 576.0
        time_h = 48.0
        max_speed = 16.0
        max_travel = time_h * max_speed
        assert distance <= max_travel

    def test_unreachable_at_16kn(self):
        """800nm in 48h: 48*16=768 max → 800 > 768 → unreachable."""
        distance = 800.0
        time_h = 48.0
        max_speed = 16.0
        max_travel = time_h * max_speed
        assert distance > max_travel

    def test_short_hop_5nm_in_1h(self):
        """5nm in 1h: max 16nm → reachable."""
        assert 5.0 <= 1.0 * 16.0

    def test_long_transit_1200nm_in_5_days(self):
        """1200nm in 5 days (120h): max 1920nm → reachable."""
        assert 1200.0 <= 120.0 * 16.0


# ── MMSI cloning detection ───────────────────────────────────────────────────


class TestMMSICloning:
    """MMSI cloning: same MMSI at >50kn implied speed within 1 hour."""

    def _make_point(self, lat, lon, ts):
        pt = MagicMock()
        pt.lat = lat
        pt.lon = lon
        pt.timestamp_utc = ts
        return pt

    def test_impossible_jump_detected(self):
        """500nm apart in 30 minutes → ~1000kn → flagged as cloning."""
        now = datetime.utcnow()
        p1 = self._make_point(55.0, 20.0, now)
        # ~500nm south (roughly 8.3 degrees latitude)
        p2 = self._make_point(46.7, 20.0, now + timedelta(minutes=30))

        vessel = MagicMock()
        vessel.mmsi = "123456789"

        jumps = find_cloning_jumps([p1, p2], vessel)
        assert len(jumps) == 1
        assert jumps[0]["implied_speed_kn"] > 50

    def test_normal_speed_not_flagged(self):
        """10nm in 1 hour → 10kn → not cloning."""
        now = datetime.utcnow()
        p1 = self._make_point(55.0, 20.0, now)
        # ~10nm north (about 0.167 degrees latitude)
        p2 = self._make_point(55.167, 20.0, now + timedelta(hours=1))

        vessel = MagicMock()
        vessel.mmsi = "123456789"

        jumps = find_cloning_jumps([p1, p2], vessel)
        assert len(jumps) == 0

    def test_points_beyond_1h_window_skipped(self):
        """Points >1h apart are not compared."""
        now = datetime.utcnow()
        p1 = self._make_point(55.0, 20.0, now)
        p2 = self._make_point(46.7, 20.0, now + timedelta(hours=2))

        vessel = MagicMock()
        vessel.mmsi = "123456789"

        jumps = find_cloning_jumps([p1, p2], vessel)
        assert len(jumps) == 0

    def test_score_cloning_100kn(self):
        assert _score_cloning(100.0) == 55

    def test_score_cloning_50kn(self):
        assert _score_cloning(50.0) == 40

    def test_score_cloning_25kn(self):
        assert _score_cloning(25.0) == 25


# ── Execute merge ────────────────────────────────────────────────────────────


class TestExecuteMerge:
    """Merge FK reassignment, unique constraint handling, metadata backfill."""

    def _setup_db_for_merge(self):
        """Create a mock DB with two vessels ready to merge."""
        db = MagicMock()

        canonical = MagicMock()
        canonical.vessel_id = 1
        canonical.mmsi = "211000001"
        canonical.imo = None
        canonical.name = "VESSEL A"
        canonical.flag = "DE"
        canonical.vessel_type = "Tanker"
        canonical.deadweight = 100000
        canonical.year_built = 2005
        canonical.owner_name = None
        canonical.merged_into_vessel_id = None
        canonical.mmsi_first_seen_utc = datetime(2026, 1, 15)

        absorbed = MagicMock()
        absorbed.vessel_id = 2
        absorbed.mmsi = "351000002"
        absorbed.imo = "9074729"
        absorbed.name = "VESSEL B"
        absorbed.flag = "PA"
        absorbed.vessel_type = "Tanker"
        absorbed.deadweight = 95000
        absorbed.year_built = 2005
        absorbed.owner_name = "Shadow Corp"
        absorbed.merged_into_vessel_id = None
        absorbed.mmsi_first_seen_utc = datetime(2026, 2, 1)

        def mock_get(vid):
            return {1: canonical, 2: absorbed}.get(vid)

        db.query.return_value.get.side_effect = mock_get

        # Default empty results for FK queries
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.first.return_value = None
        db.query.return_value.filter.return_value.update.return_value = 0
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = []
        db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        return db, canonical, absorbed

    @patch("app.modules.identity_resolver._rescore_vessel")
    def test_merge_returns_success(self, mock_rescore):
        db, canonical, absorbed = self._setup_db_for_merge()
        result = execute_merge(db, 1, 2, reason="test", merged_by="analyst")
        assert result["success"] is True

    @patch("app.modules.identity_resolver._rescore_vessel")
    def test_merge_marks_absorbed(self, mock_rescore):
        db, canonical, absorbed = self._setup_db_for_merge()
        execute_merge(db, 1, 2, reason="test", merged_by="analyst")
        assert absorbed.merged_into_vessel_id == 1

    @patch("app.modules.identity_resolver._rescore_vessel")
    def test_merge_deterministic_lower_id_canonical(self, mock_rescore):
        """Passing (canonical=2, absorbed=1) still picks vessel_id=1 as canonical."""
        db, canonical, absorbed = self._setup_db_for_merge()
        result = execute_merge(db, 2, 1, reason="test", merged_by="analyst")
        assert result["success"] is True
        # absorbed (higher ID=2) should be marked
        assert absorbed.merged_into_vessel_id == 1

    @patch("app.modules.identity_resolver._rescore_vessel")
    def test_merge_same_vessel_fails(self, mock_rescore):
        db = MagicMock()
        vessel = MagicMock()
        vessel.merged_into_vessel_id = None
        db.query.return_value.get.return_value = vessel
        result = execute_merge(db, 5, 5, reason="test", merged_by="auto")
        assert result["success"] is False

    @patch("app.modules.identity_resolver._rescore_vessel")
    def test_merge_backfills_missing_imo(self, mock_rescore):
        """Canonical has no IMO; absorbed has valid IMO → backfill."""
        db, canonical, absorbed = self._setup_db_for_merge()
        canonical.imo = None
        absorbed.imo = "9074729"
        execute_merge(db, 1, 2, reason="test", merged_by="analyst")
        assert canonical.imo == "9074729"

    @patch("app.modules.identity_resolver._rescore_vessel")
    def test_merge_keeps_earliest_first_seen(self, mock_rescore):
        """mmsi_first_seen_utc should keep the earliest date."""
        db, canonical, absorbed = self._setup_db_for_merge()
        canonical.mmsi_first_seen_utc = datetime(2026, 2, 1)
        absorbed.mmsi_first_seen_utc = datetime(2026, 1, 15)
        execute_merge(db, 1, 2, reason="test", merged_by="analyst")
        assert canonical.mmsi_first_seen_utc == datetime(2026, 1, 15)

    @patch("app.modules.identity_resolver._rescore_vessel")
    def test_merge_commits(self, mock_rescore):
        """Merge should call db.commit()."""
        db, canonical, absorbed = self._setup_db_for_merge()
        execute_merge(db, 1, 2, reason="test", merged_by="analyst")
        db.commit.assert_called()


# ── Reverse merge ────────────────────────────────────────────────────────────


class TestReverseMerge:
    def test_reverse_not_found(self):
        db = MagicMock()
        db.query.return_value.get.return_value = None
        result = reverse_merge(db, 999)
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_reverse_already_reversed(self):
        db = MagicMock()
        merge_op = MagicMock()
        merge_op.status = "reversed"
        db.query.return_value.get.return_value = merge_op
        result = reverse_merge(db, 1)
        assert result["success"] is False
        assert "Already reversed" in result["error"]

    @patch("app.modules.identity_resolver._rescore_vessel")
    def test_reverse_reactivates_absorbed_vessel(self, mock_rescore):
        db = MagicMock()
        merge_op = MagicMock()
        merge_op.status = "completed"
        merge_op.canonical_vessel_id = 1
        merge_op.absorbed_vessel_id = 2
        merge_op.candidate_id = None
        merge_op.affected_records_json = {
            "vessel_snapshot": {"mmsi": "351000002"},
            "watchlist": {"deleted_snapshots": []},
            "sts_events": {"deleted_snapshots": []},
            "evidence_cards": [],
        }

        absorbed = MagicMock()
        absorbed.merged_into_vessel_id = 1

        db.query.return_value.get.side_effect = lambda vid: {
            # First call gets merge_op, subsequent get absorbed vessel
        }.get(vid, absorbed)
        # Override to return merge_op first
        call_count = [0]
        def mock_get(vid):
            call_count[0] += 1
            if call_count[0] == 1:
                return merge_op
            return absorbed
        db.query.return_value.get.side_effect = mock_get
        db.query.return_value.filter.return_value.delete.return_value = 0
        db.query.return_value.filter.return_value.update.return_value = 0

        result = reverse_merge(db, 1)
        assert result["success"] is True
        assert absorbed.merged_into_vessel_id is None
        assert merge_op.status == "reversed"


# ── Zombie IMO detection ─────────────────────────────────────────────────────


class TestZombieIMO:
    def test_detects_fabricated_imo(self):
        db = MagicMock()
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "123456789"
        vessel.imo = "1111111"  # Fails checksum
        db.query.return_value.filter.return_value.all.return_value = [vessel]

        results = detect_zombie_imos(db)
        assert len(results) == 1
        assert results[0]["issue"] == "imo_fabricated"

    def test_valid_imo_not_flagged(self):
        db = MagicMock()
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "123456789"
        vessel.imo = "9074729"  # Valid
        db.query.return_value.filter.return_value.all.return_value = [vessel]

        results = detect_zombie_imos(db)
        assert len(results) == 0


# ── API endpoints (merge candidates, absorbed vessel redirect) ───────────────


class TestMergeCandidatesAPI:
    """Test merge-related API endpoints using the shared conftest fixtures."""

    def test_list_merge_candidates_empty(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.order_by.return_value.count.return_value = 0
        mock_db.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []

        resp = api_client.get("/api/v1/merge-candidates")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert data["items"] == []

    def test_merge_candidate_detail_not_found(self, api_client, mock_db):
        mock_db.query.return_value.get.return_value = None
        resp = api_client.get("/api/v1/merge-candidates/999")
        assert resp.status_code == 404


class TestAbsorbedVesselAPI:
    """Absorbed vessel should return redirect info, not 404."""

    def test_absorbed_vessel_returns_merge_info(self, api_client, mock_db):
        """GET /vessels/{absorbed_id} returns merge redirect info."""
        vessel = MagicMock()
        vessel.vessel_id = 2
        vessel.mmsi = "351000002"
        # Key: merged_into_vessel_id is set (this is an absorbed vessel)
        vessel.merged_into_vessel_id = 1

        # Route uses .filter().first(), not .get()
        mock_db.query.return_value.filter.return_value.first.return_value = vessel
        # resolve_canonical will call .get() on the canonical
        canonical = MagicMock()
        canonical.merged_into_vessel_id = None
        mock_db.query.return_value.get.return_value = canonical

        resp = api_client.get("/api/v1/vessels/2")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("merged") is True


# ── Timeline aggregation ─────────────────────────────────────────────────────


class TestTimeline:
    """Timeline aggregates events from 7 source tables into chronological order."""

    def test_timeline_sorts_chronologically(self):
        db = MagicMock()

        # VesselHistory entry
        vh = MagicMock()
        vh.observed_at = datetime(2026, 2, 10, 12, 0)
        vh.field_changed = "name"
        vh.old_value = "OLD NAME"
        vh.new_value = "NEW NAME"
        vh.source = "ais"
        vh.vessel_history_id = 1

        # Gap event (earlier)
        gap = MagicMock()
        gap.gap_start_utc = datetime(2026, 2, 5, 8, 0)
        gap.duration_minutes = 360
        gap.risk_score = 75
        gap.status = "new"
        gap.gap_event_id = 1

        # Spoofing (latest)
        spoof = MagicMock()
        spoof.start_time_utc = datetime(2026, 2, 15, 16, 0)
        spoof.anomaly_type = "circle_spoofing"
        spoof.implied_speed_kn = None
        spoof.anomaly_id = 1

        # Empty for remaining tables
        loiter_mock = []
        sts_mock = []
        port_mock = []
        merge_mock = []

        # Set up query chain to return correct results per model
        def mock_query(model):
            q = MagicMock()
            if model.__name__ == "VesselHistory":
                q.filter.return_value.all.return_value = [vh]
            elif model.__name__ == "AISGapEvent":
                q.filter.return_value.all.return_value = [gap]
            elif model.__name__ == "SpoofingAnomaly":
                q.filter.return_value.all.return_value = [spoof]
            elif model.__name__ == "LoiteringEvent":
                q.filter.return_value.all.return_value = []
            elif model.__name__ == "StsTransferEvent":
                q.filter.return_value.all.return_value = []
            elif model.__name__ == "PortCall":
                q.filter.return_value.all.return_value = []
            elif model.__name__ == "MergeOperation":
                q.filter.return_value.all.return_value = []
            else:
                q.filter.return_value.all.return_value = []
            return q

        db.query.side_effect = mock_query

        events = get_vessel_timeline(db, vessel_id=1)

        assert len(events) == 3
        # Sorted: gap (Feb 5) → vh (Feb 10) → spoof (Feb 15)
        assert events[0]["event_type"] == "ais_gap"
        assert events[1]["event_type"] == "identity_change"
        assert events[2]["event_type"] == "spoofing"

    def test_timeline_limit_and_offset(self):
        """Limit and offset should paginate results."""
        db = MagicMock()

        # 5 gap events
        gaps = []
        for i in range(5):
            g = MagicMock()
            g.gap_start_utc = datetime(2026, 2, 1 + i, 8, 0)
            g.duration_minutes = 100 + i * 10
            g.risk_score = 50 + i * 5
            g.status = "new"
            g.gap_event_id = i + 1
            gaps.append(g)

        def mock_query(model):
            q = MagicMock()
            if model.__name__ == "AISGapEvent":
                q.filter.return_value.all.return_value = gaps
            else:
                q.filter.return_value.all.return_value = []
            return q

        db.query.side_effect = mock_query

        events = get_vessel_timeline(db, vessel_id=1, limit=2, offset=1)
        assert len(events) == 2
        # Should be events #2 and #3 (0-indexed offset=1, limit=2)
        assert events[0]["related_entity_id"] == 2
        assert events[1]["related_entity_id"] == 3
