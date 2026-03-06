"""Tests for voyage prediction — route templates and destination prediction."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.modules.voyage_predictor import (
    jaccard_similarity,
    _extract_subsequences,
)


# ── Tests: jaccard_similarity ────────────────────────────────────────

class TestJaccardSimilarity:
    def test_identical_sets(self):
        assert jaccard_similarity({1, 2, 3}, {1, 2, 3}) == 1.0

    def test_disjoint_sets(self):
        assert jaccard_similarity({1, 2}, {3, 4}) == 0.0

    def test_partial_overlap(self):
        result = jaccard_similarity({1, 2, 3}, {2, 3, 4})
        assert abs(result - 0.5) < 0.01  # 2/4

    def test_empty_sets(self):
        assert jaccard_similarity(set(), set()) == 0.0

    def test_one_empty_set(self):
        assert jaccard_similarity({1, 2}, set()) == 0.0

    def test_superset(self):
        result = jaccard_similarity({1, 2, 3, 4}, {1, 2})
        assert abs(result - 0.5) < 0.01  # 2/4

    def test_single_element_match(self):
        assert jaccard_similarity({1}, {1}) == 1.0


# ── Tests: _extract_subsequences ─────────────────────────────────────

class TestExtractSubsequences:
    def test_extracts_all_subsequences(self):
        seq = [1, 2, 3, 4]
        subseqs = _extract_subsequences(seq, min_length=3)
        assert (1, 2, 3) in subseqs
        assert (2, 3, 4) in subseqs
        assert (1, 2, 3, 4) in subseqs
        assert len(subseqs) == 3

    def test_min_length_filter(self):
        seq = [1, 2, 3]
        subseqs = _extract_subsequences(seq, min_length=4)
        assert subseqs == []

    def test_exact_min_length(self):
        seq = [1, 2, 3]
        subseqs = _extract_subsequences(seq, min_length=3)
        assert subseqs == [(1, 2, 3)]

    def test_longer_sequence(self):
        seq = [1, 2, 3, 4, 5]
        subseqs = _extract_subsequences(seq, min_length=3)
        # length 3: 3 subseqs, length 4: 2 subseqs, length 5: 1 subseq = 6 total
        assert len(subseqs) == 6


# ── Tests: build_route_templates ─────────────────────────────────────

class TestBuildRouteTemplates:
    def test_no_port_calls(self):
        from app.modules.voyage_predictor import build_route_templates

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = build_route_templates(db)
        assert result["templates_created"] == 0
        assert result["vessels_analyzed"] == 0

    def test_counts_vessels(self):
        from app.modules.voyage_predictor import build_route_templates

        db = MagicMock()
        # Create port calls for 2 vessels with same route
        pc1 = MagicMock(vessel_id=1, port_id=10, arrival_utc=MagicMock())
        pc2 = MagicMock(vessel_id=1, port_id=20, arrival_utc=MagicMock())
        pc3 = MagicMock(vessel_id=1, port_id=30, arrival_utc=MagicMock())
        pc4 = MagicMock(vessel_id=2, port_id=10, arrival_utc=MagicMock())
        pc5 = MagicMock(vessel_id=2, port_id=20, arrival_utc=MagicMock())
        pc6 = MagicMock(vessel_id=2, port_id=30, arrival_utc=MagicMock())

        v1 = MagicMock(vessel_id=1, vessel_type="Tanker")
        v2 = MagicMock(vessel_id=2, vessel_type="Tanker")

        def query_side_effect(model):
            q = MagicMock()
            model_name = model.__name__ if hasattr(model, '__name__') else str(model)
            if model_name == "PortCall":
                q.filter.return_value.order_by.return_value.all.return_value = [pc1, pc2, pc3, pc4, pc5, pc6]
            elif model_name == "Vessel":
                q.filter.return_value.all.return_value = [v1, v2]
            elif model_name == "RouteTemplate":
                # Dedup check — no existing templates
                q.filter.return_value.all.return_value = []
            return q

        db.query.side_effect = query_side_effect

        result = build_route_templates(db)
        assert result["vessels_analyzed"] == 2
        assert result["sequences_found"] > 0


# ── Tests: predict_next_destination ──────────────────────────────────

class TestPredictNextDestination:
    def test_no_port_calls(self):
        from app.modules.voyage_predictor import predict_next_destination

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        result = predict_next_destination(db, vessel_id=1)
        assert result is None

    def test_single_port_call(self):
        from app.modules.voyage_predictor import predict_next_destination

        db = MagicMock()
        pc = MagicMock(port_id=10)
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [pc]

        result = predict_next_destination(db, vessel_id=1)
        assert result is None

    def test_no_matching_template(self):
        from app.modules.voyage_predictor import predict_next_destination

        db = MagicMock()
        pc1 = MagicMock(port_id=10, arrival_utc=MagicMock())
        pc2 = MagicMock(port_id=20, arrival_utc=MagicMock())

        def query_side_effect(model):
            q = MagicMock()
            model_name = model.__name__ if hasattr(model, '__name__') else str(model)
            if model_name == "PortCall":
                q.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [pc1, pc2]
            elif model_name == "RouteTemplate":
                q.all.return_value = []
            return q

        db.query.side_effect = query_side_effect

        result = predict_next_destination(db, vessel_id=1)
        assert result is None

    def test_returns_prediction_with_matching_template(self):
        from app.modules.voyage_predictor import predict_next_destination

        db = MagicMock()
        pc1 = MagicMock(port_id=10, arrival_utc=MagicMock())
        pc2 = MagicMock(port_id=20, arrival_utc=MagicMock())
        pc3 = MagicMock(port_id=30, arrival_utc=MagicMock())

        template = MagicMock()
        template.template_id = 1
        template.route_ports_json = [10, 20, 30, 40]
        template.vessel_type = "Tanker"

        def query_side_effect(model):
            q = MagicMock()
            model_name = model.__name__ if hasattr(model, '__name__') else str(model)
            if model_name == "PortCall":
                q.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [pc3, pc2, pc1]
            elif model_name == "RouteTemplate":
                q.all.return_value = [template]
            elif model_name == "AISPoint":
                q.filter.return_value.order_by.return_value.first.return_value = None
            elif model_name == "Corridor":
                q.filter.return_value.all.return_value = []
            return q

        db.query.side_effect = query_side_effect

        result = predict_next_destination(db, vessel_id=1)
        assert result is not None
        assert result["predicted_port_id"] == 40
        assert result["confidence"] >= 0.7


# ── Tests: _find_existing_template ───────────────────────────────────

class TestFindExistingTemplate:
    def test_finds_matching_template(self):
        from app.modules.voyage_predictor import _find_existing_template

        db = MagicMock()
        existing = MagicMock()
        existing.route_ports_json = [10, 20, 30]
        db.query.return_value.filter.return_value.all.return_value = [existing]

        result = _find_existing_template(db, "Tanker", [10, 20, 30])
        assert result is existing

    def test_returns_none_no_match(self):
        from app.modules.voyage_predictor import _find_existing_template

        db = MagicMock()
        existing = MagicMock()
        existing.route_ports_json = [10, 20, 30]
        db.query.return_value.filter.return_value.all.return_value = [existing]

        result = _find_existing_template(db, "Tanker", [40, 50, 60])
        assert result is None

    def test_returns_none_empty(self):
        from app.modules.voyage_predictor import _find_existing_template

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        result = _find_existing_template(db, "Tanker", [10, 20, 30])
        assert result is None
