"""Tests for alert deduplication engine."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.alert_group import AlertGroup
from app.modules.alert_dedup_engine import (
    _recalculate_group_stats,
    assign_to_group,
    compute_group_key,
    dissolve_group,
    merge_groups,
    run_dedup_pass,
    update_group_max_score,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alert(
    gap_event_id=1,
    vessel_id=1,
    corridor_id=None,
    gap_start_utc=None,
    risk_score=50,
    **kwargs,
):
    a = MagicMock()
    a.gap_event_id = gap_event_id
    a.vessel_id = vessel_id
    a.corridor_id = corridor_id
    a.gap_start_utc = gap_start_utc or datetime(2026, 3, 1, 12, 0)
    a.risk_score = risk_score
    a.alert_group_id = None
    for k, v in kwargs.items():
        setattr(a, k, v)
    return a


# ---------------------------------------------------------------------------
# compute_group_key tests
# ---------------------------------------------------------------------------


class TestComputeGroupKey:
    def test_same_vessel_corridor_week_produces_same_key(self):
        a1 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 1))
        a2 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 3))
        assert compute_group_key(a1) == compute_group_key(a2)

    def test_different_weeks_produce_different_keys(self):
        a1 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 1))
        a2 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 15))
        assert compute_group_key(a1) != compute_group_key(a2)

    def test_different_vessels_produce_different_keys(self):
        a1 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 1))
        a2 = _make_alert(vessel_id=2, corridor_id=5, gap_start_utc=datetime(2026, 3, 1))
        assert compute_group_key(a1) != compute_group_key(a2)

    def test_different_corridors_produce_different_keys(self):
        a1 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 1))
        a2 = _make_alert(vessel_id=1, corridor_id=6, gap_start_utc=datetime(2026, 3, 1))
        assert compute_group_key(a1) != compute_group_key(a2)

    def test_none_corridor_handled(self):
        a = _make_alert(vessel_id=1, corridor_id=None, gap_start_utc=datetime(2026, 3, 1))
        key = compute_group_key(a)
        assert isinstance(key, str)
        assert len(key) == 64  # SHA256 hex truncated to 64

    def test_custom_time_window(self):
        a1 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 1))
        a2 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 3))
        # With 1-day window, different days should produce different keys
        config = {"time_window_days": 1}
        assert compute_group_key(a1, config) != compute_group_key(a2, config)

    def test_same_day_same_key_with_1_day_window(self):
        a1 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 1, 0, 0))
        a2 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 1, 23, 59))
        config = {"time_window_days": 1}
        assert compute_group_key(a1, config) == compute_group_key(a2, config)

    def test_timezone_aware_datetime_stripped(self):
        a1 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 1, tzinfo=UTC))
        a2 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 1))
        assert compute_group_key(a1) == compute_group_key(a2)

    def test_key_is_deterministic(self):
        a = _make_alert(vessel_id=42, corridor_id=7, gap_start_utc=datetime(2026, 1, 15))
        k1 = compute_group_key(a)
        k2 = compute_group_key(a)
        assert k1 == k2


# ---------------------------------------------------------------------------
# assign_to_group tests
# ---------------------------------------------------------------------------


class TestAssignToGroup:
    def test_creates_new_group_when_none_exists(self):
        db = MagicMock()
        alert = _make_alert(gap_event_id=10, vessel_id=1, corridor_id=5, risk_score=70)

        # No existing group found
        db.query.return_value.filter.return_value.first.return_value = None

        group = assign_to_group(db, alert)

        assert group.vessel_id == 1
        assert group.corridor_id == 5
        assert group.alert_count == 1
        assert group.max_risk_score == 70
        assert group.primary_alert_id == 10
        db.add.assert_called_once()
        db.flush.assert_called_once()

    def test_adds_to_existing_group(self):
        db = MagicMock()
        alert = _make_alert(
            gap_event_id=20, vessel_id=1, corridor_id=5,
            risk_score=30, gap_start_utc=datetime(2026, 3, 3),
        )

        existing_group = MagicMock(spec=AlertGroup)
        existing_group.group_id = 1
        existing_group.alert_count = 2
        existing_group.max_risk_score = 50
        existing_group.first_seen_utc = datetime(2026, 3, 1)
        existing_group.last_seen_utc = datetime(2026, 3, 2)
        existing_group.primary_alert_id = 10
        db.query.return_value.filter.return_value.first.return_value = existing_group

        result = assign_to_group(db, alert)

        assert result.alert_count == 3
        assert result.max_risk_score == 50  # unchanged, 30 < 50
        assert result.primary_alert_id == 10  # unchanged
        assert result.last_seen_utc == datetime(2026, 3, 3)

    def test_updates_primary_alert_when_higher_score(self):
        db = MagicMock()
        alert = _make_alert(
            gap_event_id=20, vessel_id=1, corridor_id=5,
            risk_score=80, gap_start_utc=datetime(2026, 3, 3),
        )

        existing_group = MagicMock(spec=AlertGroup)
        existing_group.group_id = 1
        existing_group.alert_count = 2
        existing_group.max_risk_score = 50
        existing_group.first_seen_utc = datetime(2026, 3, 1)
        existing_group.last_seen_utc = datetime(2026, 3, 2)
        existing_group.primary_alert_id = 10
        db.query.return_value.filter.return_value.first.return_value = existing_group

        result = assign_to_group(db, alert)

        assert result.max_risk_score == 80
        assert result.primary_alert_id == 20

    def test_updates_first_seen_when_earlier(self):
        db = MagicMock()
        alert = _make_alert(
            gap_event_id=20, vessel_id=1, corridor_id=5,
            risk_score=30, gap_start_utc=datetime(2026, 2, 28),
        )

        existing_group = MagicMock(spec=AlertGroup)
        existing_group.group_id = 1
        existing_group.alert_count = 2
        existing_group.max_risk_score = 50
        existing_group.first_seen_utc = datetime(2026, 3, 1)
        existing_group.last_seen_utc = datetime(2026, 3, 2)
        existing_group.primary_alert_id = 10
        db.query.return_value.filter.return_value.first.return_value = existing_group

        result = assign_to_group(db, alert)

        assert result.first_seen_utc == datetime(2026, 2, 28)

    def test_zero_risk_score_handled(self):
        db = MagicMock()
        alert = _make_alert(gap_event_id=30, vessel_id=1, risk_score=0)
        db.query.return_value.filter.return_value.first.return_value = None

        group = assign_to_group(db, alert)
        assert group.max_risk_score == 0
        assert group.primary_alert_id == 30

    def test_none_risk_score_treated_as_zero(self):
        db = MagicMock()
        alert = _make_alert(gap_event_id=31, vessel_id=1, risk_score=None)
        db.query.return_value.filter.return_value.first.return_value = None

        group = assign_to_group(db, alert)
        assert group.max_risk_score == 0


# ---------------------------------------------------------------------------
# run_dedup_pass tests
# ---------------------------------------------------------------------------


class TestRunDedupPass:
    @patch("app.modules.alert_dedup_engine.settings")
    def test_returns_empty_when_disabled(self, mock_settings):
        mock_settings.ALERT_DEDUP_ENABLED = False
        db = MagicMock()
        result = run_dedup_pass(db)
        assert result == {"groups_created": 0, "alerts_grouped": 0, "existing_groups_updated": 0}

    @patch("app.modules.alert_dedup_engine.settings")
    def test_returns_empty_when_no_ungrouped(self, mock_settings):
        mock_settings.ALERT_DEDUP_ENABLED = True
        mock_settings.ALERT_DEDUP_TIME_WINDOW_DAYS = 7
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []
        result = run_dedup_pass(db)
        assert result["alerts_grouped"] == 0

    @patch("app.modules.alert_dedup_engine.assign_to_group")
    @patch("app.modules.alert_dedup_engine.settings")
    def test_groups_alerts_into_new_groups(self, mock_settings, mock_assign):
        mock_settings.ALERT_DEDUP_ENABLED = True
        mock_settings.ALERT_DEDUP_TIME_WINDOW_DAYS = 7

        db = MagicMock()

        # Two ungrouped alerts in same bucket
        db.execute.return_value.fetchall.return_value = [(1,), (2,)]

        alert1 = _make_alert(gap_event_id=1, vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 1))
        alert2 = _make_alert(gap_event_id=2, vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 2))

        db.query.return_value.filter.return_value.all.return_value = [alert1, alert2]
        # First lookup: no existing group; second: found (same key, group was created by first)
        existing_group = MagicMock(spec=AlertGroup, group_id=1, alert_count=1, max_risk_score=50,
                                   first_seen_utc=datetime(2026, 3, 1), last_seen_utc=datetime(2026, 3, 1),
                                   primary_alert_id=1)
        db.query.return_value.filter.return_value.first.side_effect = [None, existing_group]

        mock_assign.return_value = existing_group

        result = run_dedup_pass(db)

        assert result["alerts_grouped"] == 2
        assert result["groups_created"] == 1
        assert result["existing_groups_updated"] == 1


# ---------------------------------------------------------------------------
# merge_groups tests
# ---------------------------------------------------------------------------


class TestMergeGroups:
    def test_merge_two_groups(self):
        db = MagicMock()
        g1 = MagicMock(spec=AlertGroup, group_id=1)
        g2 = MagicMock(spec=AlertGroup, group_id=2)
        db.query.return_value.filter.return_value.all.return_value = [g1, g2]
        # For recalculate
        db.execute.return_value.fetchall.return_value = [
            (100, datetime(2026, 3, 1), 80),
            (101, datetime(2026, 3, 2), 60),
        ]

        result = merge_groups(db, [1, 2])

        assert result.group_id == 1
        db.delete.assert_called_once_with(g2)
        db.commit.assert_called_once()

    def test_merge_requires_two_groups(self):
        db = MagicMock()
        with pytest.raises(ValueError, match="at least 2"):
            merge_groups(db, [1])

    def test_merge_not_found_raises(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [MagicMock(group_id=1)]
        with pytest.raises(ValueError, match="not found"):
            merge_groups(db, [1, 999])


# ---------------------------------------------------------------------------
# dissolve_group tests
# ---------------------------------------------------------------------------


class TestDissolveGroup:
    def test_dissolve_removes_group(self):
        db = MagicMock()
        group = MagicMock(spec=AlertGroup, group_id=1)
        db.query.return_value.filter.return_value.first.return_value = group

        dissolve_group(db, 1)

        db.delete.assert_called_once_with(group)
        db.commit.assert_called_once()

    def test_dissolve_not_found_raises(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(ValueError, match="not found"):
            dissolve_group(db, 999)


# ---------------------------------------------------------------------------
# update_group_max_score tests
# ---------------------------------------------------------------------------


class TestUpdateGroupMaxScore:
    def test_recalculates_max_score(self):
        db = MagicMock()
        group = MagicMock(spec=AlertGroup, group_id=1)
        db.query.return_value.filter.return_value.first.return_value = group
        db.execute.return_value.fetchall.return_value = [
            (100, datetime(2026, 3, 1), 80),
            (101, datetime(2026, 3, 2), 90),
            (102, datetime(2026, 3, 3), 60),
        ]

        update_group_max_score(db, 1)

        assert group.max_risk_score == 90
        assert group.primary_alert_id == 101
        assert group.alert_count == 3
        db.commit.assert_called_once()

    def test_no_group_does_nothing(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        update_group_max_score(db, 999)
        db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# _recalculate_group_stats tests
# ---------------------------------------------------------------------------


class TestRecalculateGroupStats:
    def test_empty_members_resets_stats(self):
        db = MagicMock()
        group = MagicMock(spec=AlertGroup, group_id=1)
        db.execute.return_value.fetchall.return_value = []

        _recalculate_group_stats(db, group)

        assert group.alert_count == 0
        assert group.max_risk_score == 0
        assert group.primary_alert_id is None

    def test_handles_string_timestamps(self):
        db = MagicMock()
        group = MagicMock(spec=AlertGroup, group_id=1)
        db.execute.return_value.fetchall.return_value = [
            (100, "2026-03-01T00:00:00", 50),
            (101, "2026-03-02T00:00:00", 70),
        ]

        _recalculate_group_stats(db, group)

        assert group.max_risk_score == 70
        assert group.primary_alert_id == 101
        assert group.alert_count == 2

    def test_handles_none_risk_score(self):
        db = MagicMock()
        group = MagicMock(spec=AlertGroup, group_id=1)
        db.execute.return_value.fetchall.return_value = [
            (100, datetime(2026, 3, 1), None),
        ]

        _recalculate_group_stats(db, group)

        assert group.max_risk_score == 0
        assert group.alert_count == 1


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestDedupEndpoints:
    def test_post_dedup(self, api_client, mock_db):
        mock_db.execute.return_value.fetchall.return_value = []
        resp = api_client.post("/api/v1/alerts/dedup")
        assert resp.status_code == 200
        data = resp.json()
        assert "groups_created" in data

    def test_get_alert_groups_empty(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value = mock_db.query.return_value
        mock_db.query.return_value.order_by.return_value = mock_db.query.return_value
        mock_db.query.return_value.count.return_value = 0
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/alert-groups")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_get_alert_groups_with_filters(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value = mock_db.query.return_value
        mock_db.query.return_value.order_by.return_value = mock_db.query.return_value
        mock_db.query.return_value.count.return_value = 0
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/alert-groups?vessel_id=1&status=active&min_score=50")
        assert resp.status_code == 200

    def test_get_alert_group_detail_not_found(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.get("/api/v1/alert-groups/999")
        assert resp.status_code == 404

    def test_get_alert_group_detail_found(self, api_client, mock_db):
        group = MagicMock(spec=AlertGroup)
        group.group_id = 1
        group.vessel_id = 10
        group.corridor_id = 5
        group.group_key = "abc"
        group.primary_alert_id = 100
        group.alert_count = 2
        group.first_seen_utc = datetime(2026, 3, 1)
        group.last_seen_utc = datetime(2026, 3, 2)
        group.max_risk_score = 85
        group.status = "active"
        group.created_at = datetime(2026, 3, 1)
        mock_db.query.return_value.filter.return_value.first.return_value = group
        mock_db.execute.return_value.fetchall.return_value = [
            (100, 10, datetime(2026, 3, 1), datetime(2026, 3, 1, 12), 720, 85, "new"),
            (101, 10, datetime(2026, 3, 2), datetime(2026, 3, 2, 6), 360, 60, "new"),
        ]
        resp = api_client.get("/api/v1/alert-groups/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["group_id"] == 1
        assert len(data["members"]) == 2

    def test_dismiss_group(self, api_client, mock_db):
        group = MagicMock(spec=AlertGroup)
        group.group_id = 1
        group.alert_count = 3
        group.status = "active"
        mock_db.query.return_value.filter.return_value.first.return_value = group
        resp = api_client.post("/api/v1/alert-groups/1/dismiss")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "dismissed"
        assert group.status == "dismissed"

    def test_dismiss_group_not_found(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.post("/api/v1/alert-groups/999/dismiss")
        assert resp.status_code == 404

    def test_group_verdict_true_positive(self, api_client, mock_db):
        group = MagicMock(spec=AlertGroup)
        group.group_id = 1
        group.alert_count = 3
        group.status = "active"
        mock_db.query.return_value.filter.return_value.first.return_value = group
        resp = api_client.post(
            "/api/v1/alert-groups/1/verdict",
            json={"verdict": "true_positive"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "true_positive"
        assert data["status"] == "resolved"

    def test_group_verdict_false_positive(self, api_client, mock_db):
        group = MagicMock(spec=AlertGroup)
        group.group_id = 1
        group.alert_count = 3
        group.status = "active"
        mock_db.query.return_value.filter.return_value.first.return_value = group
        resp = api_client.post(
            "/api/v1/alert-groups/1/verdict",
            json={"verdict": "false_positive"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "false_positive"
        assert data["status"] == "dismissed"

    def test_group_verdict_invalid(self, api_client, mock_db):
        group = MagicMock(spec=AlertGroup)
        group.group_id = 1
        group.status = "active"
        mock_db.query.return_value.filter.return_value.first.return_value = group
        resp = api_client.post(
            "/api/v1/alert-groups/1/verdict",
            json={"verdict": "invalid_value"},
        )
        assert resp.status_code == 400

    def test_group_verdict_not_found(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None
        resp = api_client.post(
            "/api/v1/alert-groups/999/verdict",
            json={"verdict": "true_positive"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestDedupConfig:
    def test_default_config_values(self):
        from app.config import Settings

        s = Settings()
        assert s.ALERT_DEDUP_ENABLED is True
        assert s.ALERT_DEDUP_TIME_WINDOW_DAYS == 7
        assert s.ALERT_DEDUP_MIN_GROUP_SIZE == 2

    def test_custom_time_window_via_config(self):
        # Pick two dates 8 days apart: different in 7-day window, same in 365-day
        a1 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 1))
        a2 = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 9))
        # Default 7-day window: different buckets
        assert compute_group_key(a1) != compute_group_key(a2)
        # With 365-day window: same bucket (both within same year-long period)
        config = {"time_window_days": 365}
        assert compute_group_key(a1, config) == compute_group_key(a2, config)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_alert_with_no_corridor(self):
        a = _make_alert(vessel_id=1, corridor_id=None, gap_start_utc=datetime(2026, 3, 1))
        key = compute_group_key(a)
        assert isinstance(key, str)
        assert len(key) == 64

    def test_single_alert_group(self):
        db = MagicMock()
        alert = _make_alert(gap_event_id=1, vessel_id=1, risk_score=50)
        db.query.return_value.filter.return_value.first.return_value = None

        group = assign_to_group(db, alert)
        assert group.alert_count == 1

    def test_group_key_length(self):
        a = _make_alert(vessel_id=1, corridor_id=5, gap_start_utc=datetime(2026, 3, 1))
        key = compute_group_key(a)
        assert len(key) == 64  # SHA256 hex truncated to 64 chars


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestAlertGroupModel:
    def test_model_tablename(self):
        assert AlertGroup.__tablename__ == "alert_groups"

    def test_model_has_required_columns(self):
        col_names = {c.name for c in AlertGroup.__table__.columns}
        expected = {
            "group_id", "vessel_id", "corridor_id", "group_key",
            "primary_alert_id", "alert_count", "first_seen_utc",
            "last_seen_utc", "max_risk_score", "status", "created_at",
        }
        assert expected.issubset(col_names)

    def test_group_key_is_unique(self):
        col = AlertGroup.__table__.c.group_key
        assert col.unique is True

    def test_status_default(self):
        col = AlertGroup.__table__.c.status
        assert col.default.arg == "active"
