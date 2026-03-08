"""Tests for GET /merge-chains endpoint."""

from unittest.mock import MagicMock, patch


class TestMergeChainsAPI:
    def test_empty_returns_empty(self, api_client):
        with (
            patch("app.modules.merge_chain.get_merge_chains", return_value=[]),
            patch("app.modules.merge_chain.get_merge_chain_count", return_value=0),
        ):
            resp = api_client.get("/api/v1/merge-chains")
            assert resp.status_code == 200
            data = resp.json()
            assert data == {"items": [], "total": 0}

    def test_paginated_response_with_nodes_and_edges(self, api_client, mock_db):
        chain = MagicMock()
        chain.chain_id = 1
        chain.vessel_ids_json = [10, 20, 30]
        chain.links_json = [100, 101]
        chain.chain_length = 3
        chain.confidence = 0.85
        chain.confidence_band = "HIGH"
        chain.created_at = None
        chain.evidence_json = {}

        mock_vessel_10 = MagicMock()
        mock_vessel_10.vessel_id = 10
        mock_vessel_10.mmsi = "111111111"
        mock_vessel_10.name = "VESSEL_A"
        mock_vessel_20 = MagicMock()
        mock_vessel_20.vessel_id = 20
        mock_vessel_20.mmsi = "222222222"
        mock_vessel_20.name = "VESSEL_B"
        mock_vessel_30 = MagicMock()
        mock_vessel_30.vessel_id = 30
        mock_vessel_30.mmsi = "333333333"
        mock_vessel_30.name = "VESSEL_C"

        mc_100 = MagicMock()
        mc_100.candidate_id = 100
        mc_100.vessel_a_id = 10
        mc_100.vessel_b_id = 20
        mc_100.confidence_score = 0.9
        mc_101 = MagicMock()
        mc_101.candidate_id = 101
        mc_101.vessel_a_id = 20
        mc_101.vessel_b_id = 30
        mc_101.confidence_score = 0.85

        # mock_db.query(Vessel).filter(...).all() and query(MergeCandidate).filter(...).all()
        mock_db.query.return_value.filter.return_value.all.side_effect = [
            [mock_vessel_10, mock_vessel_20, mock_vessel_30],  # vessels
            [mc_100, mc_101],  # merge candidates
        ]

        with (
            patch("app.modules.merge_chain.get_merge_chains", return_value=[chain]),
            patch("app.modules.merge_chain.get_merge_chain_count", return_value=1),
        ):
            resp = api_client.get("/api/v1/merge-chains?skip=0&limit=10")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1
            assert len(data["items"]) == 1
            item = data["items"][0]
            assert item["chain_id"] == 1
            assert len(item["nodes"]) == 3
            assert len(item["edges"]) == 2
            assert item["nodes"][0]["mmsi"] == "111111111"
            assert item["nodes"][0]["name"] == "VESSEL_A"
            assert item["edges"][0]["source_id"] == 10
            assert item["edges"][0]["target_id"] == 20
            assert item["edges"][0]["confidence"] == 0.9
            assert item["edges"][1]["candidate_id"] == 101

    def test_filter_by_confidence_band(self, api_client):
        with (
            patch("app.modules.merge_chain.get_merge_chains", return_value=[]) as mock_get,
            patch("app.modules.merge_chain.get_merge_chain_count", return_value=0) as mock_count,
        ):
            resp = api_client.get("/api/v1/merge-chains?confidence_band=HIGH")
            assert resp.status_code == 200
            # Verify confidence_band was passed through
            mock_get.assert_called_once()
            _, kwargs = mock_get.call_args
            assert kwargs["confidence_band"] == "HIGH"
            mock_count.assert_called_once()
            _, count_kwargs = mock_count.call_args
            assert count_kwargs["confidence_band"] == "HIGH"

    def test_filter_by_min_confidence(self, api_client):
        with (
            patch("app.modules.merge_chain.get_merge_chains", return_value=[]) as mock_get,
            patch("app.modules.merge_chain.get_merge_chain_count", return_value=0),
        ):
            resp = api_client.get("/api/v1/merge-chains?min_confidence=0.7")
            assert resp.status_code == 200
            _, kwargs = mock_get.call_args
            assert kwargs["min_confidence"] == 0.7

    def test_missing_vessels_get_null_fields(self, api_client, mock_db):
        """When a vessel_id in the chain has no matching Vessel row, node gets null mmsi/name."""
        chain = MagicMock()
        chain.chain_id = 2
        chain.vessel_ids_json = [99]
        chain.links_json = []
        chain.chain_length = 1
        chain.confidence = 0.5
        chain.confidence_band = "LOW"
        chain.created_at = None
        chain.evidence_json = {}

        # No vessels returned for vessel_id=99
        mock_db.query.return_value.filter.return_value.all.return_value = []

        with (
            patch("app.modules.merge_chain.get_merge_chains", return_value=[chain]),
            patch("app.modules.merge_chain.get_merge_chain_count", return_value=1),
        ):
            resp = api_client.get("/api/v1/merge-chains")
            assert resp.status_code == 200
            item = resp.json()["items"][0]
            assert len(item["nodes"]) == 1
            assert item["nodes"][0]["vessel_id"] == 99
            assert item["nodes"][0]["mmsi"] is None
            assert item["nodes"][0]["name"] is None
