"""Tests for cargo inference — draught-based laden/ballast detection."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────

def _make_vessel(vessel_id=1, vessel_type="Tanker", deadweight=120000):
    v = MagicMock()
    v.vessel_id = vessel_id
    v.vessel_type = vessel_type
    v.deadweight = deadweight
    return v


def _make_ais_point(draught, ts=None):
    p = MagicMock()
    p.draught = draught
    p.timestamp_utc = ts or datetime(2026, 1, 15, 12, 0, 0)
    return p


# ── Tests: _get_max_draught ──────────────────────────────────────────

class TestGetMaxDraught:
    def test_vlcc_by_type(self):
        from app.modules.cargo_inference import _get_max_draught
        assert _get_max_draught("VLCC", None) == 22.0

    def test_suezmax_by_type(self):
        from app.modules.cargo_inference import _get_max_draught
        assert _get_max_draught("Suezmax Tanker", None) == 17.0

    def test_aframax_by_type(self):
        from app.modules.cargo_inference import _get_max_draught
        assert _get_max_draught("Aframax", None) == 15.0

    def test_panamax_by_type(self):
        from app.modules.cargo_inference import _get_max_draught
        assert _get_max_draught("Panamax", None) == 14.0

    def test_tanker_generic(self):
        from app.modules.cargo_inference import _get_max_draught
        assert _get_max_draught("tanker", None) == 16.0

    def test_crude_oil_tanker(self):
        from app.modules.cargo_inference import _get_max_draught
        assert _get_max_draught("Crude_oil_tanker", None) == 18.0

    def test_fallback_to_dwt_vlcc(self):
        from app.modules.cargo_inference import _get_max_draught
        assert _get_max_draught(None, 250_000) == 22.0

    def test_fallback_to_dwt_suezmax(self):
        from app.modules.cargo_inference import _get_max_draught
        assert _get_max_draught(None, 130_000) == 17.0

    def test_fallback_to_dwt_aframax(self):
        from app.modules.cargo_inference import _get_max_draught
        assert _get_max_draught(None, 90_000) == 15.0

    def test_fallback_to_dwt_panamax(self):
        from app.modules.cargo_inference import _get_max_draught
        assert _get_max_draught(None, 65_000) == 14.0

    def test_fallback_to_dwt_general(self):
        from app.modules.cargo_inference import _get_max_draught
        assert _get_max_draught(None, 20_000) == 12.0

    def test_default_fallback(self):
        from app.modules.cargo_inference import _get_max_draught
        assert _get_max_draught(None, None) == 15.0

    def test_unknown_type_uses_dwt(self):
        from app.modules.cargo_inference import _get_max_draught
        assert _get_max_draught("Container Ship", 200_000) == 22.0


# ── Tests: infer_cargo_state ─────────────────────────────────────────

class TestInferCargoState:
    def _setup_db(self, vessel, latest_point, recent_call=None, sts_event=None):
        db = MagicMock()

        def query_side_effect(model):
            q = MagicMock()
            model_name = model.__name__ if hasattr(model, '__name__') else str(model)

            if model_name == "Vessel":
                q.filter.return_value.first.return_value = vessel
            elif model_name == "AISPoint":
                q.filter.return_value.order_by.return_value.first.return_value = latest_point
            elif model_name == "PortCall":
                join_q = MagicMock()
                join_q.filter.return_value.order_by.return_value.first.return_value = recent_call
                q.join.return_value = join_q
            elif model_name == "StsTransferEvent":
                q.filter.return_value.first.return_value = sts_event
            return q

        db.query.side_effect = query_side_effect
        return db

    def test_returns_empty_when_vessel_not_found(self):
        from app.modules.cargo_inference import infer_cargo_state

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        result = infer_cargo_state(db, 999)
        assert result == {}

    def test_returns_empty_when_no_draught_data(self):
        from app.modules.cargo_inference import infer_cargo_state

        vessel = _make_vessel()
        db = self._setup_db(vessel, None)
        result = infer_cargo_state(db, 1)
        assert result == {}

    def test_laden_state_high_draught(self):
        from app.modules.cargo_inference import infer_cargo_state

        vessel = _make_vessel(vessel_type="Tanker", deadweight=120000)
        point = _make_ais_point(draught=14.0)
        db = self._setup_db(vessel, point)

        result = infer_cargo_state(db, 1)
        assert result["state"] == "laden"
        assert result["draught_m"] == 14.0
        assert result["laden_ratio"] > 0.6

    def test_ballast_state_low_draught(self):
        from app.modules.cargo_inference import infer_cargo_state

        vessel = _make_vessel(vessel_type="Tanker", deadweight=120000)
        point = _make_ais_point(draught=5.0)
        db = self._setup_db(vessel, point)

        result = infer_cargo_state(db, 1)
        assert result["state"] == "ballast"
        assert result["laden_ratio"] <= 0.6

    def test_risk_score_for_russian_terminal_sts(self):
        from app.modules.cargo_inference import infer_cargo_state

        vessel = _make_vessel(vessel_type="Tanker", deadweight=120000)
        point = _make_ais_point(draught=14.0)  # laden
        recent_call = MagicMock()
        sts_event = MagicMock()

        db = self._setup_db(vessel, point, recent_call=recent_call, sts_event=sts_event)

        result = infer_cargo_state(db, 1)
        assert result["state"] == "laden"
        assert result["risk_score"] == 15
        assert result.get("russian_terminal_sts") is True

    def test_no_risk_score_ballast(self):
        from app.modules.cargo_inference import infer_cargo_state

        vessel = _make_vessel(vessel_type="Tanker", deadweight=120000)
        point = _make_ais_point(draught=3.0)
        db = self._setup_db(vessel, point)

        result = infer_cargo_state(db, 1)
        assert result["state"] == "ballast"
        assert result["risk_score"] == 0

    def test_non_numeric_draught_returns_empty(self):
        from app.modules.cargo_inference import infer_cargo_state

        vessel = _make_vessel()
        point = _make_ais_point(draught="invalid")
        db = self._setup_db(vessel, point)

        result = infer_cargo_state(db, 1)
        assert result == {}
