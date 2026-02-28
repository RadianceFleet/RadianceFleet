"""Tests for F1: N+1 query fixes in routes.py endpoints.

Verifies that merge-candidates, corridors, and stats endpoints
return correct data after optimization from per-row queries to
batched/aggregated SQL queries.
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Merge Candidates — batch vessel lookup via IN query
# ---------------------------------------------------------------------------

class TestMergeCandidatesOptimized:
    def test_merge_candidates_returns_vessel_info(self, api_client, mock_db):
        """Merge candidates endpoint returns correct vessel MMSI/name
        after switching from per-candidate queries to a single IN query."""
        from app.models.merge_candidate import MergeCandidate
        from app.models.vessel import Vessel

        # Create mock candidates
        c1 = MagicMock()
        c1.candidate_id = 1
        c1.vessel_a_id = 10
        c1.vessel_b_id = 20
        c1.distance_nm = 5.0
        c1.time_delta_hours = 2.0
        c1.confidence_score = 80
        c1.match_reasons_json = {"imo": True}
        c1.satellite_corroboration_json = None
        c1.status = "pending"
        c1.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        c1.resolved_at = None
        c1.resolved_by = None

        c2 = MagicMock()
        c2.candidate_id = 2
        c2.vessel_a_id = 10  # Same vessel_a as c1
        c2.vessel_b_id = 30
        c2.distance_nm = 8.0
        c2.time_delta_hours = 4.0
        c2.confidence_score = 60
        c2.match_reasons_json = {"name": True}
        c2.satellite_corroboration_json = None
        c2.status = "pending"
        c2.created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
        c2.resolved_at = None
        c2.resolved_by = None

        # Mock vessels
        va = MagicMock()
        va.vessel_id = 10
        va.mmsi = "123456789"
        va.name = "VESSEL A"

        vb = MagicMock()
        vb.vessel_id = 20
        vb.mmsi = "987654321"
        vb.name = "VESSEL B"

        vc = MagicMock()
        vc.vessel_id = 30
        vc.mmsi = "555555555"
        vc.name = "VESSEL C"

        # Setup mock DB: order_by -> count/offset/limit for candidates
        query_mc = MagicMock()
        query_mc.count.return_value = 2
        query_mc.offset.return_value.limit.return_value.all.return_value = [c1, c2]

        # Setup vessel query: filter(IN) returns all 3 vessels
        query_vessel = MagicMock()
        query_vessel.all.return_value = [va, vb, vc]

        call_count = [0]

        def side_effect(model):
            m = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                # MergeCandidate query
                m.order_by.return_value = query_mc
                m.order_by.return_value.filter.return_value = query_mc
                return m
            else:
                # Vessel IN query
                m.filter.return_value = query_vessel
                return m

        mock_db.query.side_effect = side_effect

        resp = api_client.get("/api/v1/merge-candidates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2
        assert data["items"][0]["vessel_a"]["mmsi"] == "123456789"
        assert data["items"][0]["vessel_b"]["mmsi"] == "987654321"
        assert data["items"][1]["vessel_b"]["mmsi"] == "555555555"

    def test_merge_candidates_empty(self, api_client, mock_db):
        """Empty merge candidates list works correctly."""
        query_mc = MagicMock()
        query_mc.count.return_value = 0
        query_mc.offset.return_value.limit.return_value.all.return_value = []

        mock_db.query.return_value.order_by.return_value = query_mc

        resp = api_client.get("/api/v1/merge-candidates")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_merge_candidates_missing_vessel(self, api_client, mock_db):
        """When a vessel referenced by a candidate doesn't exist, mmsi/name are None."""
        c1 = MagicMock()
        c1.candidate_id = 1
        c1.vessel_a_id = 10
        c1.vessel_b_id = 999  # Missing vessel
        c1.distance_nm = 5.0
        c1.time_delta_hours = 2.0
        c1.confidence_score = 80
        c1.match_reasons_json = {}
        c1.satellite_corroboration_json = None
        c1.status = "pending"
        c1.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        c1.resolved_at = None
        c1.resolved_by = None

        va = MagicMock()
        va.vessel_id = 10
        va.mmsi = "123456789"
        va.name = "VESSEL A"

        query_mc = MagicMock()
        query_mc.count.return_value = 1
        query_mc.offset.return_value.limit.return_value.all.return_value = [c1]

        query_vessel = MagicMock()
        query_vessel.all.return_value = [va]  # vessel_b (999) not found

        call_count = [0]

        def side_effect(model):
            m = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                m.order_by.return_value = query_mc
                m.order_by.return_value.filter.return_value = query_mc
                return m
            else:
                m.filter.return_value = query_vessel
                return m

        mock_db.query.side_effect = side_effect

        resp = api_client.get("/api/v1/merge-candidates")
        assert resp.status_code == 200
        data = resp.json()
        item = data["items"][0]
        assert item["vessel_a"]["mmsi"] == "123456789"
        assert item["vessel_b"]["mmsi"] is None
        assert item["vessel_b"]["name"] is None


# ---------------------------------------------------------------------------
# Corridors — aggregated stats via GROUP BY
# ---------------------------------------------------------------------------

class TestCorridorStatsOptimized:
    def test_corridor_stats_aggregated(self, api_client, mock_db):
        """Corridor stats are computed via single GROUP BY query."""
        from app.models.corridor import Corridor

        c1 = MagicMock()
        c1.corridor_id = 1
        c1.name = "Kadetrinne"
        c1.corridor_type = MagicMock()
        c1.corridor_type.value = "export_route"
        c1.risk_weight = 1.5
        c1.is_jamming_zone = False
        c1.description = "Test corridor"

        c2 = MagicMock()
        c2.corridor_id = 2
        c2.name = "Laconian Gulf"
        c2.corridor_type = MagicMock()
        c2.corridor_type.value = "sts_zone"
        c2.risk_weight = 1.8
        c2.is_jamming_zone = True
        c2.description = "STS zone"

        # Stats aggregation rows: (corridor_id, alert_7d, alert_30d, avg_score)
        stats_row_1 = (1, 5, 15, 42.3)
        stats_row_2 = (2, 10, 30, 68.7)

        # Track calls to mock_db.query
        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            m = MagicMock()
            if call_count[0] == 1:
                # Corridor query
                m.count.return_value = 2
                m.offset.return_value.limit.return_value.all.return_value = [c1, c2]
                return m
            elif call_count[0] == 2:
                # AISGapEvent aggregation query (multi-arg: corridor_id, sum, sum, avg)
                m.filter.return_value.group_by.return_value.all.return_value = [stats_row_1, stats_row_2]
                return m
            return m

        mock_db.query.side_effect = query_side_effect

        resp = api_client.get("/api/v1/corridors")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        items = data["items"]
        assert items[0]["corridor_id"] == 1
        assert items[0]["name"] == "Kadetrinne"
        assert items[0]["alert_count_7d"] == 5
        assert items[0]["alert_count_30d"] == 15
        assert items[0]["avg_risk_score"] == 42.3
        assert items[1]["alert_count_7d"] == 10
        assert items[1]["avg_risk_score"] == 68.7

    def test_corridor_no_stats(self, api_client, mock_db):
        """Corridors with no gap events get zero counts and None avg."""
        c1 = MagicMock()
        c1.corridor_id = 1
        c1.name = "Empty Corridor"
        c1.corridor_type = MagicMock()
        c1.corridor_type.value = "export_route"
        c1.risk_weight = 1.0
        c1.is_jamming_zone = False
        c1.description = None

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            m = MagicMock()
            if call_count[0] == 1:
                m.count.return_value = 1
                m.offset.return_value.limit.return_value.all.return_value = [c1]
                return m
            elif call_count[0] == 2:
                # No stats rows
                m.filter.return_value.group_by.return_value.all.return_value = []
                return m
            return m

        mock_db.query.side_effect = query_side_effect

        resp = api_client.get("/api/v1/corridors")
        assert resp.status_code == 200
        data = resp.json()
        items = data["items"]
        assert items[0]["alert_count_7d"] == 0
        assert items[0]["alert_count_30d"] == 0
        assert items[0]["avg_risk_score"] is None


# ---------------------------------------------------------------------------
# Stats — SQL aggregation instead of loading all rows
# ---------------------------------------------------------------------------

class TestStatsOptimized:
    def test_stats_returns_correct_counts(self, api_client, mock_db):
        """Stats endpoint returns correct risk band counts using SQL aggregation."""
        # count aggregation result: (total, critical, high, medium, low)
        count_result = MagicMock()
        count_result.__getitem__ = lambda self, i: [100, 10, 25, 40, 25][i]

        # status aggregation
        status_new = MagicMock()
        status_new.value = "new"
        status_review = MagicMock()
        status_review.value = "under_review"
        status_rows = [(status_new, 60), (status_review, 40)]

        # corridor aggregation
        corridor_rows = [(1, 50), (2, 30), (None, 20)]

        # multi-gap subquery
        multi_gap_count = 5
        distinct_vessels = 42

        call_count = [0]

        def query_side_effect(*args, **kwargs):
            call_count[0] += 1
            m = MagicMock()
            if call_count[0] == 1:
                # Main AISGapEvent query -> with_entities for counts
                m.with_entities.return_value.first.return_value = count_result
                # Chain for status grouping
                m.with_entities.return_value.group_by.return_value.all.return_value = status_rows
                return m
            elif call_count[0] <= 3:
                # Status or corridor with_entities query
                m.with_entities.return_value.group_by.return_value.all.return_value = corridor_rows
                return m
            elif call_count[0] == 4:
                # multi_gap_subq
                m.filter.return_value.group_by.return_value.having.return_value.subquery.return_value = "subq"
                return m
            elif call_count[0] == 5:
                # count from subquery
                m.select_from.return_value.scalar.return_value = multi_gap_count
                return m
            elif call_count[0] == 6:
                # distinct vessels
                m.scalar.return_value = distinct_vessels
                return m
            return m

        mock_db.query.side_effect = query_side_effect

        resp = api_client.get("/api/v1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["alert_counts"]["total"] == 100
        assert data["alert_counts"]["critical"] == 10
        assert data["alert_counts"]["high"] == 25
        assert data["alert_counts"]["medium"] == 40
        assert data["alert_counts"]["low"] == 25

    def test_stats_empty_database(self, api_client, mock_db):
        """Stats endpoint handles empty database gracefully."""
        count_result = MagicMock()
        count_result.__getitem__ = lambda self, i: [0, 0, 0, 0, 0][i]

        call_count = [0]

        def query_side_effect(*args):
            call_count[0] += 1
            m = MagicMock()
            if call_count[0] == 1:
                m.with_entities.return_value.first.return_value = count_result
                m.with_entities.return_value.group_by.return_value.all.return_value = []
                return m
            elif call_count[0] <= 3:
                m.with_entities.return_value.group_by.return_value.all.return_value = []
                return m
            elif call_count[0] == 4:
                m.filter.return_value.group_by.return_value.having.return_value.subquery.return_value = "subq"
                return m
            elif call_count[0] == 5:
                m.select_from.return_value.scalar.return_value = 0
                return m
            elif call_count[0] == 6:
                m.scalar.return_value = 0
                return m
            return m

        mock_db.query.side_effect = query_side_effect

        resp = api_client.get("/api/v1/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["alert_counts"]["total"] == 0
        assert data["alert_counts"]["critical"] == 0
