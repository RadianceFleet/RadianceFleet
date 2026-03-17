"""Tests for incremental scoring pipeline (Task 41)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from app.models.vessel_scoring_state import VesselScoringState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vessel(vessel_id: int, merged_into: int | None = None) -> MagicMock:
    v = MagicMock()
    v.vessel_id = vessel_id
    v.merged_into_vessel_id = merged_into
    return v


def _make_gap_event(gap_event_id: int, vessel_id: int, risk_score: int = 0) -> MagicMock:
    g = MagicMock()
    g.gap_event_id = gap_event_id
    g.vessel_id = vessel_id
    g.risk_score = risk_score
    g.risk_breakdown_json = None
    g.corridor_id = None
    g.is_feed_outage = False
    g.original_vessel_id = vessel_id
    g.gap_start_utc = datetime(2024, 6, 1, tzinfo=UTC)
    g.pre_gap_sog = 12.0
    return g


def _make_scoring_state(
    vessel_id: int, dirty: bool = True, scoring_version: str | None = None
) -> MagicMock:
    s = MagicMock(spec=VesselScoringState)
    s.vessel_id = vessel_id
    s.dirty = dirty
    s.last_scored_at = None
    s.last_data_hash = None
    s.scoring_version = scoring_version
    return s


# ---------------------------------------------------------------------------
# VesselScoringState model tests
# ---------------------------------------------------------------------------


class TestVesselScoringStateModel:
    def test_model_tablename(self):
        assert VesselScoringState.__tablename__ == "vessel_scoring_states"

    def test_model_has_vessel_id_pk(self):
        cols = {c.name for c in VesselScoringState.__table__.columns}
        assert "vessel_id" in cols

    def test_model_has_dirty_column(self):
        cols = {c.name for c in VesselScoringState.__table__.columns}
        assert "dirty" in cols

    def test_model_has_scoring_version(self):
        cols = {c.name for c in VesselScoringState.__table__.columns}
        assert "scoring_version" in cols

    def test_model_has_last_scored_at(self):
        cols = {c.name for c in VesselScoringState.__table__.columns}
        assert "last_scored_at" in cols

    def test_model_has_last_data_hash(self):
        cols = {c.name for c in VesselScoringState.__table__.columns}
        assert "last_data_hash" in cols

    def test_model_has_last_ais_point_id(self):
        cols = {c.name for c in VesselScoringState.__table__.columns}
        assert "last_ais_point_id" in cols

    def test_model_has_last_gap_event_id(self):
        cols = {c.name for c in VesselScoringState.__table__.columns}
        assert "last_gap_event_id" in cols


# ---------------------------------------------------------------------------
# mark_vessel_dirty tests
# ---------------------------------------------------------------------------


class TestMarkVesselDirty:
    def test_marks_existing_state_dirty(self):
        from app.modules.incremental_scorer import mark_vessel_dirty

        db = MagicMock()
        existing = _make_scoring_state(1, dirty=False)
        db.query.return_value.filter.return_value.first.return_value = existing
        mark_vessel_dirty(db, 1)
        assert existing.dirty is True
        db.flush.assert_called_once()

    def test_creates_new_state_when_not_exists(self):
        from app.modules.incremental_scorer import mark_vessel_dirty

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        mark_vessel_dirty(db, 42)
        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.vessel_id == 42
        assert added.dirty is True


# ---------------------------------------------------------------------------
# mark_vessels_dirty_bulk tests
# ---------------------------------------------------------------------------


class TestMarkVesselsDirtyBulk:
    def test_bulk_empty_set_no_op(self):
        from app.modules.incremental_scorer import mark_vessels_dirty_bulk

        db = MagicMock()
        mark_vessels_dirty_bulk(db, set())
        db.execute.assert_not_called()
        db.flush.assert_not_called()

    def test_bulk_calls_execute_for_nonempty(self):
        from app.modules.incremental_scorer import mark_vessels_dirty_bulk

        db = MagicMock()
        mark_vessels_dirty_bulk(db, {1, 2, 3})
        db.execute.assert_called_once()
        db.flush.assert_called_once()

    def test_bulk_single_vessel(self):
        from app.modules.incremental_scorer import mark_vessels_dirty_bulk

        db = MagicMock()
        mark_vessels_dirty_bulk(db, {99})
        db.execute.assert_called_once()


# ---------------------------------------------------------------------------
# get_dirty_vessels tests
# ---------------------------------------------------------------------------


class TestGetDirtyVessels:
    def test_returns_vessel_ids(self):
        from app.modules.incremental_scorer import get_dirty_vessels

        db = MagicMock()
        row1 = MagicMock()
        row1.vessel_id = 1
        row2 = MagicMock()
        row2.vessel_id = 2
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [
            row1, row2
        ]
        result = get_dirty_vessels(db, batch_size=500)
        assert result == [1, 2]

    def test_returns_empty_when_none_dirty(self):
        from app.modules.incremental_scorer import get_dirty_vessels

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = []
        result = get_dirty_vessels(db)
        assert result == []

    def test_respects_batch_size(self):
        from app.modules.incremental_scorer import get_dirty_vessels

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = []
        get_dirty_vessels(db, batch_size=10)
        db.query.return_value.filter.return_value.limit.assert_called_with(10)


# ---------------------------------------------------------------------------
# compute_config_hash tests
# ---------------------------------------------------------------------------


class TestComputeConfigHash:
    @patch("app.modules.scoring_config.load_scoring_config")
    def test_returns_hex_string(self, mock_load):
        from app.modules.incremental_scorer import compute_config_hash

        mock_load.return_value = {"gap_duration": {"base_points": 10}}
        result = compute_config_hash()
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex

    @patch("app.modules.scoring_config.load_scoring_config")
    def test_deterministic(self, mock_load):
        from app.modules.incremental_scorer import compute_config_hash

        mock_load.return_value = {"a": 1, "b": 2}
        h1 = compute_config_hash()
        h2 = compute_config_hash()
        assert h1 == h2

    @patch("app.modules.scoring_config.load_scoring_config")
    def test_changes_when_config_changes(self, mock_load):
        from app.modules.incremental_scorer import compute_config_hash

        mock_load.return_value = {"a": 1}
        h1 = compute_config_hash()
        mock_load.return_value = {"a": 2}
        h2 = compute_config_hash()
        assert h1 != h2

    @patch("app.modules.scoring_config.load_scoring_config")
    def test_key_order_independent(self, mock_load):
        """sort_keys=True means key order doesn't matter."""
        from app.modules.incremental_scorer import compute_config_hash

        mock_load.return_value = {"z": 1, "a": 2}
        h1 = compute_config_hash()
        mock_load.return_value = {"a": 2, "z": 1}
        h2 = compute_config_hash()
        assert h1 == h2


# ---------------------------------------------------------------------------
# score_vessel_alerts tests
# ---------------------------------------------------------------------------


class TestScoreVesselAlerts:
    @patch("app.modules.risk_scoring._load_corridor_overrides", return_value={})
    @patch("app.modules.risk_scoring._merge_config_with_overrides")
    @patch("app.modules.risk_scoring.compute_gap_score")
    @patch("app.modules.risk_scoring._count_gaps_in_window", return_value=0)
    def test_scores_all_alerts_for_vessel(self, mock_count, mock_score, mock_merge, mock_overrides):
        from app.modules.incremental_scorer import score_vessel_alerts

        mock_merge.return_value = {"config": "data"}
        mock_score.return_value = (42, {"gap_duration": 20})
        db = MagicMock()
        g1 = _make_gap_event(1, 1)
        g2 = _make_gap_event(2, 1)
        db.query.return_value.filter.return_value.all.return_value = [g1, g2]

        result = score_vessel_alerts(db, vessel_id=1, config={"config": "data"})
        assert result == 2
        assert g1.risk_score == 42
        assert g2.risk_score == 42

    @patch("app.modules.risk_scoring._load_corridor_overrides", return_value={})
    @patch("app.modules.risk_scoring._merge_config_with_overrides")
    @patch("app.modules.risk_scoring.compute_gap_score")
    @patch("app.modules.risk_scoring._count_gaps_in_window", return_value=0)
    def test_skips_feed_outage_gaps(self, mock_count, mock_score, mock_merge, mock_overrides):
        from app.modules.incremental_scorer import score_vessel_alerts

        mock_merge.return_value = {}
        db = MagicMock()
        g1 = _make_gap_event(1, 1)
        g1.is_feed_outage = True
        db.query.return_value.filter.return_value.all.return_value = [g1]

        result = score_vessel_alerts(db, vessel_id=1, config={})
        assert result == 0
        mock_score.assert_not_called()

    @patch("app.modules.risk_scoring._load_corridor_overrides", return_value={})
    def test_returns_zero_for_vessel_with_no_alerts(self, mock_overrides):
        from app.modules.incremental_scorer import score_vessel_alerts

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        result = score_vessel_alerts(db, vessel_id=1, config={})
        assert result == 0


# ---------------------------------------------------------------------------
# incremental_score_alerts tests
# ---------------------------------------------------------------------------


class TestIncrementalScoreAlerts:
    @patch("app.modules.scoring_config.load_scoring_config", return_value={"a": 1})
    @patch("app.modules.incremental_scorer.compute_config_hash", return_value="abc123")
    @patch("app.modules.incremental_scorer.get_dirty_vessels")
    @patch("app.modules.incremental_scorer.score_vessel_alerts", return_value=3)
    def test_processes_only_dirty_vessels(self, mock_score, mock_dirty, mock_hash, mock_load):
        from app.modules.incremental_scorer import incremental_score_alerts

        db = MagicMock()
        # No previous scoring version
        db.query.return_value.filter.return_value.limit.return_value.scalar.return_value = None
        # Return dirty IDs once, then empty
        mock_dirty.side_effect = [[1, 2], []]
        # Scoring state lookups
        state1 = _make_scoring_state(1)
        state2 = _make_scoring_state(2)
        # Each vessel_id query returns its state
        db.query.return_value.filter.return_value.first.side_effect = [state1, state2]
        # Total vessel count
        db.query.return_value.filter.return_value.count.return_value = 5

        result = incremental_score_alerts(db)
        assert result["scored"] == 2
        assert result["skipped"] == 3
        assert result["config_changed"] is False
        assert mock_score.call_count == 2

    @patch("app.modules.scoring_config.load_scoring_config", return_value={"a": 1})
    @patch("app.modules.incremental_scorer.compute_config_hash", return_value="new_hash")
    @patch("app.modules.incremental_scorer._mark_all_vessels_dirty", return_value=10)
    @patch("app.modules.incremental_scorer.get_dirty_vessels", return_value=[])
    def test_config_change_marks_all_dirty(self, mock_dirty, mock_mark_all, mock_hash, mock_load):
        from app.modules.incremental_scorer import incremental_score_alerts

        db = MagicMock()
        # Previous version is different
        db.query.return_value.filter.return_value.limit.return_value.scalar.return_value = "old_hash"
        db.query.return_value.filter.return_value.count.return_value = 10

        result = incremental_score_alerts(db)
        assert result["config_changed"] is True
        mock_mark_all.assert_called_once_with(db)

    @patch("app.modules.scoring_config.load_scoring_config", return_value={})
    @patch("app.modules.incremental_scorer.compute_config_hash", return_value="h")
    @patch("app.modules.incremental_scorer.get_dirty_vessels", return_value=[])
    def test_no_dirty_vessels_returns_zero_scored(self, mock_dirty, mock_hash, mock_load):
        from app.modules.incremental_scorer import incremental_score_alerts

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.scalar.return_value = None
        db.query.return_value.filter.return_value.count.return_value = 100

        result = incremental_score_alerts(db)
        assert result["scored"] == 0
        assert result["skipped"] == 100

    @patch("app.modules.scoring_config.load_scoring_config", return_value={})
    @patch("app.modules.incremental_scorer.compute_config_hash", return_value="h")
    @patch("app.modules.incremental_scorer.get_dirty_vessels")
    @patch("app.modules.incremental_scorer.score_vessel_alerts", return_value=0)
    def test_creates_state_when_not_exists(self, mock_score, mock_dirty, mock_hash, mock_load):
        from app.modules.incremental_scorer import incremental_score_alerts

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.scalar.return_value = None
        mock_dirty.side_effect = [[5], []]
        # No existing state
        db.query.return_value.filter.return_value.first.return_value = None
        db.query.return_value.filter.return_value.count.return_value = 1

        result = incremental_score_alerts(db)
        assert result["scored"] == 1
        db.add.assert_called()

    @patch("app.modules.scoring_config.load_scoring_config", return_value={})
    @patch("app.modules.incremental_scorer.compute_config_hash", return_value="same")
    @patch("app.modules.incremental_scorer.get_dirty_vessels", return_value=[])
    def test_same_config_no_change_flag(self, mock_dirty, mock_hash, mock_load):
        from app.modules.incremental_scorer import incremental_score_alerts

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.scalar.return_value = "same"
        db.query.return_value.filter.return_value.count.return_value = 0

        result = incremental_score_alerts(db)
        assert result["config_changed"] is False

    @patch("app.modules.scoring_config.load_scoring_config", return_value={})
    @patch("app.modules.incremental_scorer.compute_config_hash", return_value="h")
    @patch("app.modules.incremental_scorer.get_dirty_vessels")
    @patch("app.modules.incremental_scorer.score_vessel_alerts", return_value=2)
    def test_scoring_state_cleared_after_scoring(self, mock_score, mock_dirty, mock_hash, mock_load):
        from app.modules.incremental_scorer import incremental_score_alerts

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.scalar.return_value = None
        mock_dirty.side_effect = [[7], []]
        state = _make_scoring_state(7, dirty=True)
        db.query.return_value.filter.return_value.first.return_value = state
        db.query.return_value.filter.return_value.count.return_value = 1

        incremental_score_alerts(db)
        assert state.dirty is False
        assert state.scoring_version == "h"
        assert state.last_scored_at is not None


# ---------------------------------------------------------------------------
# _mark_all_vessels_dirty tests
# ---------------------------------------------------------------------------


class TestMarkAllVesselsDirty:
    @patch("app.modules.incremental_scorer.mark_vessels_dirty_bulk")
    def test_marks_all_active_vessels(self, mock_bulk):
        from app.modules.incremental_scorer import _mark_all_vessels_dirty

        db = MagicMock()
        r1 = MagicMock()
        r1.vessel_id = 1
        r2 = MagicMock()
        r2.vessel_id = 2
        db.query.return_value.filter.return_value.all.return_value = [r1, r2]

        count = _mark_all_vessels_dirty(db)
        assert count == 2
        mock_bulk.assert_called_once()

    @patch("app.modules.incremental_scorer.mark_vessels_dirty_bulk")
    def test_no_vessels_returns_zero(self, mock_bulk):
        from app.modules.incremental_scorer import _mark_all_vessels_dirty

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        count = _mark_all_vessels_dirty(db)
        assert count == 0
        mock_bulk.assert_not_called()


# ---------------------------------------------------------------------------
# Config flag tests
# ---------------------------------------------------------------------------


class TestConfigFlag:
    def test_incremental_scoring_enabled_default(self):
        from app.config import Settings

        s = Settings(
            ADMIN_JWT_SECRET="x" * 64,
            ADMIN_PASSWORD="test",
        )
        assert s.INCREMENTAL_SCORING_ENABLED is True

    def test_incremental_scoring_batch_size_default(self):
        from app.config import Settings

        s = Settings(
            ADMIN_JWT_SECRET="x" * 64,
            ADMIN_PASSWORD="test",
        )
        assert s.INCREMENTAL_SCORING_BATCH_SIZE == 500


# ---------------------------------------------------------------------------
# Admin API endpoint tests
# ---------------------------------------------------------------------------


class TestAdminScoringStateEndpoint:
    def test_get_scoring_state(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.scalar.return_value = 10
        mock_db.query.return_value.scalar.return_value = None
        with patch(
            "app.modules.incremental_scorer.compute_config_hash",
            return_value="abcdef1234567890" * 4,
        ):
            resp = api_client.get("/api/v1/admin/scoring-state")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_vessels" in data
        assert "dirty_count" in data
        assert "config_hash" in data
        assert "incremental_enabled" in data

    def test_mark_dirty_all(self, api_client, mock_db):
        with patch(
            "app.modules.incremental_scorer._mark_all_vessels_dirty",
            return_value=5,
        ):
            resp = api_client.post(
                "/api/v1/admin/scoring-state/mark-dirty",
                json={"all": True},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["marked_dirty"] == 5

    def test_mark_dirty_specific_vessels(self, api_client, mock_db):
        with patch("app.modules.incremental_scorer.mark_vessels_dirty_bulk") as mock_bulk:
            resp = api_client.post(
                "/api/v1/admin/scoring-state/mark-dirty",
                json={"vessel_ids": [1, 2, 3]},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["marked_dirty"] == 3
        mock_bulk.assert_called_once()

    def test_mark_dirty_no_params_returns_400(self, api_client, mock_db):
        resp = api_client.post(
            "/api/v1/admin/scoring-state/mark-dirty",
            json={},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Score-alerts endpoint incremental path
# ---------------------------------------------------------------------------


class TestScoreAlertsIncremental:
    def test_incremental_path_when_enabled(self, api_client, mock_db):
        with patch(
            "app.modules.incremental_scorer.incremental_score_alerts",
            return_value={"scored": 5, "skipped": 10, "config_changed": False, "config_hash": "abc"},
        ):
            with patch("app.config.settings.INCREMENTAL_SCORING_ENABLED", True):
                resp = api_client.post("/api/v1/score-alerts")
        assert resp.status_code == 200

    def test_score_alerts_endpoint_exists(self, api_client, mock_db):
        """Verify the /score-alerts endpoint is reachable."""
        with patch(
            "app.modules.incremental_scorer.incremental_score_alerts",
            return_value={"scored": 0, "skipped": 0, "config_changed": False, "config_hash": "x"},
        ):
            resp = api_client.post("/api/v1/score-alerts")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Gap detector dirty flag integration
# ---------------------------------------------------------------------------


class TestGapDetectorDirtyFlag:
    @patch("app.modules.gap_detector.detect_gaps_for_vessel")
    @patch("app.modules.incremental_scorer.mark_vessels_dirty_bulk")
    def test_gap_detection_marks_dirty(self, mock_bulk, mock_detect):
        from app.modules.gap_detector import run_gap_detection

        mock_detect.return_value = 3
        db = MagicMock()
        v1 = _make_vessel(1)
        db.query.return_value.filter.return_value.all.return_value = [v1]
        db.query.return_value.count.return_value = 1

        run_gap_detection(db)
        mock_bulk.assert_called_once()
        assert 1 in mock_bulk.call_args[0][1]

    @patch("app.modules.gap_detector.detect_gaps_for_vessel")
    def test_gap_detection_no_gaps_no_dirty(self, mock_detect):
        from app.modules.gap_detector import run_gap_detection

        mock_detect.return_value = 0
        db = MagicMock()
        v1 = _make_vessel(1)
        db.query.return_value.filter.return_value.all.return_value = [v1]
        db.query.return_value.count.return_value = 0

        run_gap_detection(db)
        # No bulk dirty marking should happen since no gaps were found


# ---------------------------------------------------------------------------
# Merge execution dirty flag integration
# ---------------------------------------------------------------------------


class TestMergeExecutionDirtyFlag:
    """Test that merge execution marks both vessels dirty."""

    @patch("app.modules.incremental_scorer.mark_vessels_dirty_bulk")
    @patch("app.modules.merge_execution._rescore_vessel")
    @patch("app.modules.merge_execution._record_merge_history")
    @patch("app.modules.merge_execution._update_canonical_metadata")
    @patch("app.modules.merge_execution._reassign_ais_points", return_value={"count": 0, "id_range": None})
    @patch("app.modules.merge_execution._reassign_simple_fks", return_value={})
    @patch("app.modules.merge_execution._merge_vessel_history", return_value={"reassigned": 0, "duplicates_skipped": 0})
    @patch("app.modules.merge_execution._merge_sts_events", return_value={"reassigned": 0, "self_sts_deleted": 0, "duplicates_resolved": 0, "deleted_snapshots": []})
    @patch("app.modules.merge_execution._merge_watchlist", return_value={"reassigned": 0, "conflicts_resolved": 0, "deleted_snapshots": []})
    @patch("app.modules.merge_execution._annotate_evidence_cards", return_value=[])
    @patch("app.modules.identity_resolver.resolve_canonical", side_effect=lambda x, db: x)
    def test_merge_marks_both_dirty(
        self, mock_resolve, mock_ec, mock_wl, mock_sts, mock_vh,
        mock_simple, mock_ais, mock_meta, mock_hist, mock_rescore, mock_bulk
    ):
        from app.modules.merge_execution import execute_merge

        db = MagicMock()
        canonical = _make_vessel(1)
        absorbed = _make_vessel(2)
        db.query.return_value.get.side_effect = [canonical, absorbed]
        db.query.return_value.filter.return_value.update.return_value = 0
        db.query.return_value.filter.return_value.all.return_value = []

        execute_merge(db, 1, 2, reason="test", commit=True)
        mock_bulk.assert_called_once()
        call_ids = mock_bulk.call_args[0][1]
        assert 1 in call_ids
        assert 2 in call_ids


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @patch("app.modules.scoring_config.load_scoring_config", return_value={})
    @patch("app.modules.incremental_scorer.compute_config_hash", return_value="h")
    @patch("app.modules.incremental_scorer.get_dirty_vessels", return_value=[])
    def test_all_clean_returns_zero(self, mock_dirty, mock_hash, mock_load):
        from app.modules.incremental_scorer import incremental_score_alerts

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.scalar.return_value = "h"
        db.query.return_value.filter.return_value.count.return_value = 50

        result = incremental_score_alerts(db)
        assert result["scored"] == 0
        assert result["skipped"] == 50
        assert result["config_changed"] is False

    @patch("app.modules.scoring_config.load_scoring_config", return_value={})
    @patch("app.modules.incremental_scorer.compute_config_hash", return_value="h")
    @patch("app.modules.incremental_scorer.get_dirty_vessels")
    @patch("app.modules.incremental_scorer.score_vessel_alerts", return_value=0)
    def test_dirty_vessel_with_no_alerts(self, mock_score, mock_dirty, mock_hash, mock_load):
        """A vessel can be dirty but have zero gap events."""
        from app.modules.incremental_scorer import incremental_score_alerts

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.scalar.return_value = None
        mock_dirty.side_effect = [[99], []]
        state = _make_scoring_state(99)
        db.query.return_value.filter.return_value.first.return_value = state
        db.query.return_value.filter.return_value.count.return_value = 1

        result = incremental_score_alerts(db)
        assert result["scored"] == 1
        assert result["alerts_scored"] == 0
        assert state.dirty is False

    @patch("app.modules.scoring_config.load_scoring_config", return_value={})
    @patch("app.modules.incremental_scorer.compute_config_hash", return_value="new")
    @patch("app.modules.incremental_scorer._mark_all_vessels_dirty", return_value=100)
    @patch("app.modules.incremental_scorer.get_dirty_vessels")
    @patch("app.modules.incremental_scorer.score_vessel_alerts", return_value=1)
    def test_all_dirty_after_config_change(self, mock_score, mock_dirty, mock_mark_all, mock_hash, mock_load):
        from app.modules.incremental_scorer import incremental_score_alerts

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.scalar.return_value = "old"
        # Return 3 dirty then empty (simulating batch iteration)
        mock_dirty.side_effect = [[1, 2, 3], []]
        state = _make_scoring_state(1)
        db.query.return_value.filter.return_value.first.return_value = state
        db.query.return_value.filter.return_value.count.return_value = 3

        result = incremental_score_alerts(db)
        assert result["config_changed"] is True
        assert result["scored"] == 3
        mock_mark_all.assert_called_once()

    @patch("app.modules.scoring_config.load_scoring_config", return_value={})
    @patch("app.modules.incremental_scorer.compute_config_hash", return_value="h")
    @patch("app.modules.incremental_scorer.get_dirty_vessels", return_value=[])
    def test_first_run_no_previous_version(self, mock_dirty, mock_hash, mock_load):
        """When scoring_version is None everywhere, config_changed stays False."""
        from app.modules.incremental_scorer import incremental_score_alerts

        db = MagicMock()
        db.query.return_value.filter.return_value.limit.return_value.scalar.return_value = None
        db.query.return_value.filter.return_value.count.return_value = 0

        result = incremental_score_alerts(db)
        assert result["config_changed"] is False
