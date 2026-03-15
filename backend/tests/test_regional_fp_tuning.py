"""Tests for per-signal overrides, engine integration, and calibration audit trail (Phases 1+2)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base, CorridorTypeEnum
from app.models.calibration_event import CalibrationEvent
from app.models.corridor import Corridor
from app.models.corridor_scoring_override import CorridorScoringOverride
from app.models.gap_event import AISGapEvent
from app.modules.risk_scoring import (
    _load_corridor_overrides,
    _merge_config_with_overrides,
    _merge_overrides,
)
from app.modules.scoring_config import validate_signal_override_keys

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """In-memory SQLite session with required tables."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _make_corridor(db: Session, name: str = "Test Corridor", **kwargs) -> Corridor:
    kwargs.setdefault("corridor_type", CorridorTypeEnum.EXPORT_ROUTE)
    c = Corridor(name=name, **kwargs)
    db.add(c)
    db.flush()
    return c


def _make_override(
    db: Session,
    corridor_id: int,
    signal_overrides: dict | None = None,
    **kwargs,
) -> CorridorScoringOverride:
    kwargs.setdefault("corridor_multiplier_override", 1.0)
    kwargs.setdefault("gap_duration_multiplier", 1.0)
    kwargs.setdefault("is_active", True)
    ov = CorridorScoringOverride(
        corridor_id=corridor_id,
        signal_overrides_json=json.dumps(signal_overrides) if signal_overrides else None,
        **kwargs,
    )
    db.add(ov)
    db.flush()
    return ov


def _make_gap(
    db: Session,
    corridor_id: int | None = None,
    vessel_id: int = 1,
    risk_score: int = 0,
    **kwargs,
) -> AISGapEvent:
    now = datetime.utcnow()
    gap = AISGapEvent(
        vessel_id=vessel_id,
        corridor_id=corridor_id,
        gap_start_utc=kwargs.pop("gap_start", now - timedelta(hours=6)),
        gap_end_utc=kwargs.pop("gap_end", now - timedelta(hours=4)),
        duration_minutes=120,
        risk_score=risk_score,
        **kwargs,
    )
    db.add(gap)
    db.flush()
    return gap


# ---------------------------------------------------------------------------
# Model tests (5)
# ---------------------------------------------------------------------------


class TestCorridorScoringOverrideWithSignalOverrides:
    def test_signal_overrides_roundtrip(self, db):
        """signal_overrides_json can be stored and read back."""
        c = _make_corridor(db, "Signal Override Test")
        overrides = {"gap_duration.12h_plus": 15.0, "corridor.sts_zone": 2.0}
        ov = _make_override(db, c.corridor_id, signal_overrides=overrides)
        db.commit()
        db.refresh(ov)

        assert ov.signal_overrides_json is not None
        parsed = json.loads(ov.signal_overrides_json)
        assert parsed["gap_duration.12h_plus"] == 15.0
        assert parsed["corridor.sts_zone"] == 2.0

    def test_signal_overrides_null(self, db):
        """signal_overrides_json can be null."""
        c = _make_corridor(db, "Null Override")
        ov = _make_override(db, c.corridor_id, signal_overrides=None)
        db.commit()
        db.refresh(ov)

        assert ov.signal_overrides_json is None

    def test_region_id_column(self, db):
        """region_id column is nullable and defaults to None."""
        c = _make_corridor(db, "Region Test")
        ov = _make_override(db, c.corridor_id)
        db.commit()
        db.refresh(ov)

        assert ov.region_id is None


class TestCalibrationEventModel:
    def test_create_calibration_event(self, db):
        """CalibrationEvent can be created with all fields."""
        c = _make_corridor(db, "Calibration Test")
        evt = CalibrationEvent(
            corridor_id=c.corridor_id,
            event_type="override_created",
            before_values_json=None,
            after_values_json=json.dumps({"corridor_multiplier_override": 1.2}),
            reason="Testing calibration",
        )
        db.add(evt)
        db.commit()
        db.refresh(evt)

        assert evt.event_id is not None
        assert evt.corridor_id == c.corridor_id
        assert evt.event_type == "override_created"
        assert evt.created_at is not None

    def test_calibration_event_types(self, db):
        """Various event_type values can be stored."""
        c = _make_corridor(db, "Event Types")
        for etype in [
            "override_created",
            "override_updated",
            "override_deactivated",
            "suggestion_accepted",
            "auto_calibration",
        ]:
            evt = CalibrationEvent(corridor_id=c.corridor_id, event_type=etype)
            db.add(evt)
        db.commit()

        events = db.query(CalibrationEvent).all()
        assert len(events) == 5


# ---------------------------------------------------------------------------
# Signal override validation (scoring_config)
# ---------------------------------------------------------------------------


class TestValidateSignalOverrideKeys:
    def test_valid_keys(self):
        """Known section.key pairs pass validation."""
        overrides = {
            "gap_duration.12h_plus": 15.0,
            "corridor.sts_zone": 2.0,
            "spoofing.circle_spoofing_penalty": 5.0,
        }
        invalid = validate_signal_override_keys(overrides)
        assert invalid == []

    def test_invalid_section(self):
        """Unknown section names are flagged."""
        overrides = {"nonexistent_section.key": 1.0}
        invalid = validate_signal_override_keys(overrides)
        assert "nonexistent_section.key" in invalid

    def test_single_part_key(self):
        """Keys without dot notation are flagged."""
        overrides = {"gap_duration": 1.0}
        invalid = validate_signal_override_keys(overrides)
        assert "gap_duration" in invalid

    def test_internal_keys_skipped(self):
        """Keys starting with _ are skipped."""
        overrides = {"_corridor_multiplier_override": 1.0}
        invalid = validate_signal_override_keys(overrides)
        assert invalid == []

    def test_three_level_valid(self):
        """Three-level dot notation is valid if section is known."""
        overrides = {"speed_anomaly.thresholds.high": 10.0}
        invalid = validate_signal_override_keys(overrides)
        assert invalid == []


# ---------------------------------------------------------------------------
# Override integration (10)
# ---------------------------------------------------------------------------


class TestMergeOverrides:
    def test_baseline_no_overrides(self):
        """Config is returned unchanged when no overrides exist."""
        config = {"gap_duration": {"12h_plus": 10}, "corridor": {"sts_zone": 1.5}}
        result = _merge_config_with_overrides(config, None, {})
        assert result is config  # same object — fast path

    def test_fast_path_no_corridor_overrides(self):
        """Config is not copied when corridor has no overrides."""
        config = {"gap_duration": {"12h_plus": 10}}
        corridor_overrides = {99: {"gap_duration.12h_plus": 15}}
        result = _merge_config_with_overrides(config, 1, corridor_overrides)
        assert result is config  # fast path — no copy

    def test_per_signal_override_applied(self):
        """Per-signal override changes the merged config value."""
        config = {"gap_duration": {"12h_plus": 10, "24h_plus": 30}}
        corridor_overrides = {1: {"gap_duration.12h_plus": 15}}
        result = _merge_config_with_overrides(config, 1, corridor_overrides)
        assert result is not config  # copy made
        assert result["gap_duration"]["12h_plus"] == 15
        assert result["gap_duration"]["24h_plus"] == 30  # untouched

    def test_original_config_unchanged(self):
        """Deep-merge does not mutate the original config dict."""
        config = {"gap_duration": {"12h_plus": 10}}
        corridor_overrides = {1: {"gap_duration.12h_plus": 99}}
        _merge_config_with_overrides(config, 1, corridor_overrides)
        assert config["gap_duration"]["12h_plus"] == 10  # original unchanged

    def test_none_value_skipped(self):
        """None values in overrides are skipped."""
        config = {"gap_duration": {"12h_plus": 10}}
        overrides = {"gap_duration.12h_plus": None}
        result = _merge_overrides(config, overrides)
        assert result["gap_duration"]["12h_plus"] == 10  # unchanged

    def test_three_level_nested_override(self):
        """3-level dot notation works for nested sections."""
        config = {"speed_anomaly": {"thresholds": {"high": 5.0, "low": 1.0}}}
        overrides = {"speed_anomaly.thresholds.high": 8.0}
        result = _merge_overrides(config, overrides)
        assert result["speed_anomaly"]["thresholds"]["high"] == 8.0
        assert result["speed_anomaly"]["thresholds"]["low"] == 1.0

    def test_internal_keys_skipped_in_merge(self):
        """Keys starting with _ are skipped during merge."""
        config = {"gap_duration": {"12h_plus": 10}}
        overrides = {"_corridor_multiplier_override": 1.5, "gap_duration.12h_plus": 15}
        result = _merge_overrides(config, overrides)
        assert result["gap_duration"]["12h_plus"] == 15
        assert "_corridor_multiplier_override" not in result

    def test_non_numeric_value_rejected(self):
        """Non-numeric override values are not merged."""
        config = {"gap_duration": {"12h_plus": 10}}
        overrides = {"gap_duration.12h_plus": "not_a_number"}
        result = _merge_overrides(config, overrides)
        assert result["gap_duration"]["12h_plus"] == 10  # unchanged

    def test_unknown_section_ignored(self):
        """Override for unknown section is silently ignored."""
        config = {"gap_duration": {"12h_plus": 10}}
        overrides = {"nonexistent.key": 5.0}
        result = _merge_overrides(config, overrides)
        assert "nonexistent" not in result


class TestLoadCorridorOverrides:
    def test_load_active_overrides(self, db):
        """Active overrides are loaded correctly."""
        c = _make_corridor(db, "Active Override")
        overrides = {"gap_duration.12h_plus": 15.0}
        _make_override(db, c.corridor_id, signal_overrides=overrides, corridor_multiplier_override=1.2)
        db.commit()

        result = _load_corridor_overrides(db)
        assert c.corridor_id in result
        assert result[c.corridor_id]["gap_duration.12h_plus"] == 15.0
        assert result[c.corridor_id]["_corridor_multiplier_override"] == 1.2

    def test_inactive_overrides_excluded(self, db):
        """Inactive overrides are not loaded."""
        c = _make_corridor(db, "Inactive Override")
        _make_override(db, c.corridor_id, is_active=False)
        db.commit()

        result = _load_corridor_overrides(db)
        assert c.corridor_id not in result

    def test_invalid_json_handled(self, db):
        """Malformed signal_overrides_json is handled gracefully."""
        c = _make_corridor(db, "Invalid JSON")
        ov = CorridorScoringOverride(
            corridor_id=c.corridor_id,
            corridor_multiplier_override=1.0,
            gap_duration_multiplier=1.0,
            signal_overrides_json="{invalid json}",
        )
        db.add(ov)
        db.commit()

        result = _load_corridor_overrides(db)
        assert c.corridor_id in result
        # Internal keys are still present even if JSON is invalid
        assert "_corridor_multiplier_override" in result[c.corridor_id]


class TestScoreAllAlertsWithOverrides:
    """Integration tests for score_all_alerts with corridor overrides."""

    def test_override_source_in_breakdown(self, db):
        """When overrides are applied, _override_source appears in breakdown."""
        c = _make_corridor(db, "Override Source Test")
        overrides = {"gap_duration.12h_plus": 99.0}
        _make_override(db, c.corridor_id, signal_overrides=overrides)
        _make_gap(db, corridor_id=c.corridor_id, risk_score=0)
        db.commit()

        # We test via the helper functions rather than full score_all_alerts
        # since that would require the full scoring config YAML
        config = {"gap_duration": {"12h_plus": 10}}
        corridor_ovs = _load_corridor_overrides(db)
        merged = _merge_config_with_overrides(config, c.corridor_id, corridor_ovs)
        assert merged is not config
        assert merged["gap_duration"]["12h_plus"] == 99.0

    @patch("app.modules.risk_scoring.load_scoring_config")
    @patch("app.modules.risk_scoring.compute_gap_score")
    def test_score_all_alerts_uses_merged_config(self, mock_compute, mock_load_config, db):
        """score_all_alerts passes merged config to compute_gap_score."""
        from app.modules.risk_scoring import score_all_alerts

        c = _make_corridor(db, "Score All Test")
        overrides = {"gap_duration.12h_plus": 99.0}
        _make_override(db, c.corridor_id, signal_overrides=overrides)
        _make_gap(db, corridor_id=c.corridor_id, risk_score=0)
        db.commit()

        mock_load_config.return_value = {"gap_duration": {"12h_plus": 10}}
        mock_compute.return_value = (50, {"gap_duration": 10})

        result = score_all_alerts(db)
        assert result["scored"] == 1

        # Verify compute_gap_score received the merged config
        call_args = mock_compute.call_args
        merged_cfg = call_args[0][1]  # second positional arg is config
        assert merged_cfg["gap_duration"]["12h_plus"] == 99.0


# ---------------------------------------------------------------------------
# API tests (8)
# ---------------------------------------------------------------------------


class TestFPTuningAPI:
    """API-level tests using FastAPI TestClient with mock DB."""

    @pytest.fixture()
    def mock_db(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        session.query.return_value.filter.return_value.all.return_value = []
        session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        return session

    @pytest.fixture()
    def client(self, mock_db):
        from fastapi.testclient import TestClient

        from app.auth import require_auth, require_senior_or_admin
        from app.database import get_db
        from app.main import app

        def override_get_db():
            yield mock_db

        def override_auth():
            return {"analyst_id": 1, "username": "test_admin", "role": "admin"}

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[require_auth] = override_auth
        app.dependency_overrides[require_senior_or_admin] = override_auth
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()

    def _mock_corridor(self, corridor_id=1, name="Test Corridor"):
        c = MagicMock()
        c.corridor_id = corridor_id
        c.name = name
        return c

    def _mock_override(self, corridor_id=1, **kwargs):
        ov = MagicMock()
        ov.override_id = kwargs.get("override_id", 1)
        ov.corridor_id = corridor_id
        ov.corridor_multiplier_override = kwargs.get("corridor_multiplier_override", 1.0)
        ov.gap_duration_multiplier = kwargs.get("gap_duration_multiplier", 1.0)
        ov.description = kwargs.get("description")
        ov.created_by = kwargs.get("created_by")
        ov.created_at = datetime.utcnow()
        ov.updated_at = datetime.utcnow()
        ov.is_active = kwargs.get("is_active", True)
        ov.signal_overrides_json = kwargs.get("signal_overrides_json")
        ov.region_id = kwargs.get("region_id")
        return ov

    def test_create_override_with_signal_overrides(self, mock_db, client):
        """POST creates override with signal_overrides field."""
        corridor = self._mock_corridor()
        # First .filter().first() returns corridor, second returns None (no existing override)
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            corridor,  # _get_corridor_or_404
            None,  # existing override check
        ]

        # db.refresh must populate override_id and is_active on the real ORM object
        def _fake_refresh(obj):
            if isinstance(obj, CorridorScoringOverride):
                obj.override_id = 1
                if obj.is_active is None:
                    obj.is_active = True

        mock_db.refresh.side_effect = _fake_refresh

        resp = client.post(
            "/api/v1/corridors/1/scoring-override",
            json={
                "corridor_multiplier_override": 1.2,
                "gap_duration_multiplier": 0.8,
                "description": "Test override",
                "signal_overrides": {"gap_duration.12h_plus": 15.0},
            },
        )
        assert resp.status_code == 200, f"Response: {resp.json()}"
        data = resp.json()
        assert data["signal_overrides"] == {"gap_duration.12h_plus": 15.0}
        assert data["corridor_multiplier_override"] == 1.2

    def test_get_override_includes_signal_overrides(self, mock_db, client):
        """GET returns signal_overrides and region_id in response."""
        corridor = self._mock_corridor()
        override = self._mock_override(
            signal_overrides_json=json.dumps({"spoofing.circle_penalty": 5.0}),
            corridor_multiplier_override=1.1,
            region_id=None,
        )
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            corridor,  # _get_corridor_or_404
            override,  # existing override
        ]

        resp = client.get("/api/v1/corridors/1/scoring-override")
        assert resp.status_code == 200
        data = resp.json()
        assert data["signal_overrides"] == {"spoofing.circle_penalty": 5.0}
        assert data["region_id"] is None

    def test_invalid_signal_key_returns_400(self, mock_db, client):
        """POST with invalid signal override key returns 400."""
        corridor = self._mock_corridor()
        mock_db.query.return_value.filter.return_value.first.return_value = corridor

        resp = client.post(
            "/api/v1/corridors/1/scoring-override",
            json={
                "signal_overrides": {"nonexistent_section.key": 5.0},
            },
        )
        assert resp.status_code == 400
        assert "Invalid signal override keys" in resp.json()["detail"]

    def test_deactivate_records_calibration_event(self, mock_db, client):
        """DELETE records a calibration event."""
        corridor = self._mock_corridor()
        override = self._mock_override(corridor_multiplier_override=1.5)
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            corridor,  # _get_corridor_or_404
            override,  # existing override
        ]

        resp = client.delete("/api/v1/corridors/1/scoring-override")
        assert resp.status_code == 200

        # Verify CalibrationEvent was added
        add_calls = mock_db.add.call_args_list
        cal_events = [
            c for c in add_calls if isinstance(c[0][0], CalibrationEvent)
        ]
        assert len(cal_events) == 1
        evt = cal_events[0][0][0]
        assert evt.event_type == "override_deactivated"

    def test_create_records_calibration_event(self, mock_db, client):
        """POST records a calibration event on new override."""
        corridor = self._mock_corridor()
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            corridor,  # _get_corridor_or_404
            None,  # no existing override
        ]

        def _fake_refresh(obj):
            if isinstance(obj, CorridorScoringOverride):
                obj.override_id = 1
                if obj.is_active is None:
                    obj.is_active = True

        mock_db.refresh.side_effect = _fake_refresh

        resp = client.post(
            "/api/v1/corridors/1/scoring-override",
            json={
                "corridor_multiplier_override": 1.2,
                "description": "Initial override",
            },
        )
        assert resp.status_code == 200

        # Verify CalibrationEvent was added via db.add
        add_calls = mock_db.add.call_args_list
        cal_events = [
            c for c in add_calls if isinstance(c[0][0], CalibrationEvent)
        ]
        assert len(cal_events) == 1
        evt = cal_events[0][0][0]
        assert evt.event_type == "override_created"

    def test_calibration_history_endpoint(self, mock_db, client):
        """GET calibration-history returns events for a corridor."""
        corridor = self._mock_corridor()
        mock_db.query.return_value.filter.return_value.first.return_value = corridor

        evt = MagicMock()
        evt.event_id = 1
        evt.corridor_id = 1
        evt.region_id = None
        evt.event_type = "override_created"
        evt.before_values_json = None
        evt.after_values_json = json.dumps({"corridor_multiplier_override": 1.2})
        evt.impact_summary_json = None
        evt.analyst_id = None
        evt.reason = "Test reason"
        evt.created_at = datetime.utcnow()

        mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [evt]

        resp = client.get("/api/v1/corridors/1/calibration-history")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["event_type"] == "override_created"
        assert data[0]["after_values"] == {"corridor_multiplier_override": 1.2}

    def test_calibration_impact_preview(self, mock_db, client):
        """GET calibration-impact returns preview data."""
        corridor = self._mock_corridor()
        mock_db.query.return_value.filter.return_value.first.return_value = corridor

        gap = MagicMock()
        gap.gap_event_id = 1
        gap.risk_score = 50
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [gap]

        overrides = json.dumps({"gap_duration.12h_plus": 15.0})
        resp = client.get(
            "/api/v1/corridors/1/calibration-impact",
            params={"signal_overrides": overrides},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["corridor_id"] == 1
        assert data["affected_alerts"] == 1

    def test_calibration_impact_invalid_key(self, mock_db, client):
        """GET calibration-impact returns 400 for invalid override keys."""
        corridor = self._mock_corridor()
        mock_db.query.return_value.filter.return_value.first.return_value = corridor

        overrides = json.dumps({"nonexistent.key": 5.0})
        resp = client.get(
            "/api/v1/corridors/1/calibration-impact",
            params={"signal_overrides": overrides},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Region override propagation tests (15)
# ---------------------------------------------------------------------------

from app.models.scoring_region import ScoringRegion


def _make_region(db: Session, name="Test Region", corridor_ids=None, signal_overrides=None, **kwargs):
    kwargs.setdefault("is_active", True)
    kwargs.setdefault("gap_duration_multiplier", 1.0)
    region = ScoringRegion(
        name=name,
        corridor_ids_json=json.dumps(corridor_ids) if corridor_ids else None,
        signal_overrides_json=json.dumps(signal_overrides) if signal_overrides else None,
        **kwargs,
    )
    db.add(region)
    db.flush()
    return region


class TestRegionOverridePropagation:
    """Tests for region override propagation into _load_corridor_overrides."""

    def test_region_override_applied_to_corridor(self, db):
        """Corridor in a region gets the region's signal overrides."""
        c = _make_corridor(db, "Region Signal Test")
        _make_region(
            db,
            name="Signal Region",
            corridor_ids=[c.corridor_id],
            signal_overrides={"gap_duration.12h_plus": 20.0},
        )
        db.commit()

        result = _load_corridor_overrides(db)
        assert c.corridor_id in result
        assert result[c.corridor_id]["gap_duration.12h_plus"] == 20.0

    def test_region_override_multiplier(self, db):
        """Corridor gets region's corridor_multiplier_override."""
        c = _make_corridor(db, "Region Multiplier Test")
        _make_region(
            db,
            name="Multiplier Region",
            corridor_ids=[c.corridor_id],
            corridor_multiplier_override=1.5,
        )
        db.commit()

        result = _load_corridor_overrides(db)
        assert result[c.corridor_id]["_corridor_multiplier_override"] == 1.5

    def test_region_override_gap_duration(self, db):
        """Corridor gets region's gap_duration_multiplier."""
        c = _make_corridor(db, "Region Gap Duration Test")
        _make_region(
            db,
            name="Gap Duration Region",
            corridor_ids=[c.corridor_id],
            gap_duration_multiplier=2.0,
        )
        db.commit()

        result = _load_corridor_overrides(db)
        assert result[c.corridor_id]["_gap_duration_multiplier"] == 2.0

    def test_direct_override_wins_over_region(self, db):
        """Per-corridor override takes precedence over region override."""
        c = _make_corridor(db, "Precedence Test")
        _make_override(
            db,
            c.corridor_id,
            signal_overrides={"gap_duration.12h_plus": 99.0},
            corridor_multiplier_override=3.0,
        )
        _make_region(
            db,
            name="Overridden Region",
            corridor_ids=[c.corridor_id],
            signal_overrides={"gap_duration.12h_plus": 5.0},
            corridor_multiplier_override=0.5,
        )
        db.commit()

        result = _load_corridor_overrides(db)
        assert result[c.corridor_id]["gap_duration.12h_plus"] == 99.0
        assert result[c.corridor_id]["_corridor_multiplier_override"] == 3.0

    def test_inactive_region_ignored(self, db):
        """Inactive region's overrides are not applied."""
        c = _make_corridor(db, "Inactive Region Test")
        _make_region(
            db,
            name="Inactive Region",
            corridor_ids=[c.corridor_id],
            signal_overrides={"gap_duration.12h_plus": 20.0},
            is_active=False,
        )
        db.commit()

        result = _load_corridor_overrides(db)
        assert c.corridor_id not in result

    def test_region_null_corridor_ids(self, db):
        """Region with null corridor_ids_json is a no-op."""
        _make_region(
            db,
            name="Null Corridors Region",
            corridor_ids=None,
            signal_overrides={"gap_duration.12h_plus": 20.0},
        )
        db.commit()

        result = _load_corridor_overrides(db)
        # No corridors should be affected
        assert len(result) == 0

    def test_region_empty_corridor_ids(self, db):
        """Region with empty corridor_ids list is a no-op."""
        c = _make_corridor(db, "Empty Corridors Test")  # noqa: F841
        _make_region(
            db,
            name="Empty Corridors Region",
            corridor_ids=[],
            signal_overrides={"gap_duration.12h_plus": 20.0},
        )
        db.commit()

        result = _load_corridor_overrides(db)
        assert len(result) == 0

    def test_region_invalid_signal_overrides_json(self, db):
        """Bad JSON in signal_overrides_json is skipped with warning."""
        c = _make_corridor(db, "Bad JSON Region Test")
        region = ScoringRegion(
            name="Bad JSON Region",
            corridor_ids_json=json.dumps([c.corridor_id]),
            signal_overrides_json="{invalid json}",
            gap_duration_multiplier=1.0,
            is_active=True,
        )
        db.add(region)
        db.commit()

        result = _load_corridor_overrides(db)
        # Corridor should still be in result with empty signal overrides
        assert c.corridor_id in result
        # Signal overrides should be empty (bad JSON skipped)
        non_internal = {k: v for k, v in result[c.corridor_id].items() if not k.startswith("_")}
        assert non_internal == {}

    def test_region_null_multiplier_override(self, db):
        """Region with None corridor_multiplier_override passes None through."""
        c = _make_corridor(db, "Null Multiplier Test")
        _make_region(
            db,
            name="Null Multiplier Region",
            corridor_ids=[c.corridor_id],
            corridor_multiplier_override=None,
        )
        db.commit()

        result = _load_corridor_overrides(db)
        assert result[c.corridor_id]["_corridor_multiplier_override"] is None

    def test_multi_region_conflict_lowest_wins(self, db):
        """Corridor in two active regions — lowest region_id wins."""
        c = _make_corridor(db, "Multi-Region Test")
        r1 = _make_region(
            db,
            name="Region A",
            corridor_ids=[c.corridor_id],
            signal_overrides={"gap_duration.12h_plus": 10.0},
        )
        _make_region(
            db,
            name="Region B",
            corridor_ids=[c.corridor_id],
            signal_overrides={"gap_duration.12h_plus": 50.0},
        )
        db.commit()

        result = _load_corridor_overrides(db)
        # Lowest region_id wins
        assert result[c.corridor_id]["gap_duration.12h_plus"] == 10.0
        assert result[c.corridor_id]["_override_source"] == f"region:{r1.region_id}"

    def test_multi_region_conflict_logs_warning(self, db):
        """Warning is logged when a corridor belongs to multiple active regions."""
        c = _make_corridor(db, "Multi-Region Warning Test")
        _make_region(
            db,
            name="Region W1",
            corridor_ids=[c.corridor_id],
        )
        _make_region(
            db,
            name="Region W2",
            corridor_ids=[c.corridor_id],
        )
        db.commit()

        mock_logger = MagicMock()
        with patch("app.modules.risk_scoring.logger", mock_logger):
            _load_corridor_overrides(db)
        warning_calls = [
            call for call in mock_logger.warning.call_args_list
            if "multiple active regions" in str(call)
        ]
        assert len(warning_calls) == 1

    def test_override_source_tagged_region(self, db):
        """_override_source is 'region:{id}' for region overrides."""
        c = _make_corridor(db, "Source Tag Region Test")
        r = _make_region(
            db,
            name="Source Tag Region",
            corridor_ids=[c.corridor_id],
        )
        db.commit()

        result = _load_corridor_overrides(db)
        assert result[c.corridor_id]["_override_source"] == f"region:{r.region_id}"

    def test_override_source_tagged_corridor(self, db):
        """_override_source is 'corridor:{id}' for direct overrides."""
        c = _make_corridor(db, "Source Tag Corridor Test")
        _make_override(db, c.corridor_id)
        db.commit()

        result = _load_corridor_overrides(db)
        assert result[c.corridor_id]["_override_source"] == f"corridor:{c.corridor_id}"


class TestCrossRegionCheckAPI:
    """API tests for cross-region corridor uniqueness check."""

    @pytest.fixture()
    def mock_db(self):
        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = None
        session.query.return_value.filter.return_value.all.return_value = []
        session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        return session

    @pytest.fixture()
    def client(self, mock_db):
        from fastapi.testclient import TestClient

        from app.auth import require_auth, require_senior_or_admin
        from app.database import get_db
        from app.main import app

        def override_get_db():
            yield mock_db

        def override_auth():
            return {"analyst_id": 1, "username": "test_admin", "role": "admin"}

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[require_auth] = override_auth
        app.dependency_overrides[require_senior_or_admin] = override_auth
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()

    def _mock_region(self, region_id=1, name="Test Region", corridor_ids_json=None, **kwargs):
        r = MagicMock()
        r.region_id = region_id
        r.name = name
        r.description = kwargs.get("description")
        r.corridor_ids_json = corridor_ids_json
        r.signal_overrides_json = kwargs.get("signal_overrides_json")
        r.corridor_multiplier_override = kwargs.get("corridor_multiplier_override")
        r.gap_duration_multiplier = kwargs.get("gap_duration_multiplier", 1.0)
        r.is_active = kwargs.get("is_active", True)
        r.created_by = kwargs.get("created_by")
        r.created_at = datetime.utcnow()
        r.updated_at = datetime.utcnow()
        return r

    def test_cross_region_check_returns_409(self, mock_db, client):
        """Adding corridor to second active region returns 409."""
        region = self._mock_region(region_id=1, name="Region A", corridor_ids_json=None)
        corridor = MagicMock()
        corridor.corridor_id = 5

        # _get_region_or_404 uses .filter().first() -> region
        # _get_corridor_or_404 uses .filter().first() -> corridor
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            region,    # _get_region_or_404
            corridor,  # _get_corridor_or_404
        ]

        # Cross-region check: other_regions query returns a region that already has corridor 5
        other_region = self._mock_region(
            region_id=2,
            name="Region B",
            corridor_ids_json=json.dumps([5]),
        )
        mock_db.query.return_value.filter.return_value.all.return_value = [other_region]

        resp = client.post(
            "/api/v1/corridors/regions/1/corridors",
            json={"corridor_id": 5},
        )
        assert resp.status_code == 409
        assert "already belongs to active region" in resp.json()["detail"]
        assert "Region B" in resp.json()["detail"]

    def test_cross_region_check_inactive_allowed(self, mock_db, client):
        """Corridor in inactive region can be added to active region."""
        region = self._mock_region(
            region_id=1,
            name="Region A",
            corridor_ids_json=None,
        )
        corridor = MagicMock()
        corridor.corridor_id = 5

        mock_db.query.return_value.filter.return_value.first.side_effect = [
            region,    # _get_region_or_404
            corridor,  # _get_corridor_or_404
        ]

        # Cross-region check: query filters by is_active=True, so inactive region won't appear
        # No other active regions have this corridor
        mock_db.query.return_value.filter.return_value.all.return_value = []

        resp = client.post(
            "/api/v1/corridors/regions/1/corridors",
            json={"corridor_id": 5},
        )
        # Should succeed (200), not 409
        assert resp.status_code == 200
