"""Tests for merge_chain.py module — BFS detection wrapper, query helpers, and endpoint."""
from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chain(
    chain_id: int,
    vessel_ids: list[int],
    confidence: float = 80.0,
    confidence_band: str = "HIGH",
    evidence_json: dict | None = None,
):
    mc = MagicMock()
    mc.chain_id = chain_id
    mc.vessel_ids_json = vessel_ids
    mc.links_json = list(range(100, 100 + len(vessel_ids) - 1))
    mc.chain_length = len(vessel_ids)
    mc.confidence = confidence
    mc.confidence_band = confidence_band
    mc.evidence_json = evidence_json or {}
    mc.created_at = datetime.datetime(2025, 6, 1, tzinfo=datetime.timezone.utc)
    return mc


def _make_merge_candidate(
    candidate_id: int,
    vessel_a_id: int,
    vessel_b_id: int,
    confidence_score: int = 80,
    status_value: str = "auto_merged",
    created_at: datetime.datetime | None = None,
):
    mc = MagicMock()
    mc.candidate_id = candidate_id
    mc.vessel_a_id = vessel_a_id
    mc.vessel_b_id = vessel_b_id
    mc.confidence_score = confidence_score
    mc.created_at = created_at or datetime.datetime(2025, 6, 1, tzinfo=datetime.timezone.utc)
    mc.status = MagicMock()
    mc.status.value = status_value
    return mc


# ---------------------------------------------------------------------------
# Module import tests
# ---------------------------------------------------------------------------

class TestModuleImports:
    def test_import_module(self):
        from app.modules.merge_chain import detect_merge_chains
        assert callable(detect_merge_chains)

    def test_import_get_merge_chains(self):
        from app.modules.merge_chain import get_merge_chains
        assert callable(get_merge_chains)

    def test_import_serialize(self):
        from app.modules.merge_chain import serialize_merge_chain
        assert callable(serialize_merge_chain)


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------

class TestSerializeMergeChain:
    def test_basic_serialization(self):
        from app.modules.merge_chain import serialize_merge_chain
        chain = _make_chain(1, [10, 20, 30], confidence=75.0, confidence_band="HIGH")
        result = serialize_merge_chain(chain)
        assert result["chain_id"] == 1
        assert result["vessel_ids"] == [10, 20, 30]
        assert result["chain_length"] == 3
        assert result["confidence"] == 75.0
        assert result["confidence_band"] == "HIGH"
        assert result["created_at"] is not None

    def test_none_fields(self):
        from app.modules.merge_chain import serialize_merge_chain
        chain = MagicMock()
        chain.chain_id = 1
        chain.vessel_ids_json = None
        chain.links_json = None
        chain.chain_length = 0
        chain.confidence = 0.0
        chain.confidence_band = "LOW"
        chain.created_at = None
        chain.evidence_json = None
        result = serialize_merge_chain(chain)
        assert result["vessel_ids"] == []
        assert result["links"] == []
        assert result["created_at"] is None
        assert result["evidence"] == {}


# ---------------------------------------------------------------------------
# BFS detection delegation tests
# ---------------------------------------------------------------------------

class TestDetectMergeChainsModule:
    @patch("app.modules.merge_candidates.settings")
    def test_delegates_to_identity_resolver(self, mock_settings):
        """merge_chain.detect_merge_chains delegates to identity_resolver."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = False
        db = MagicMock()
        from app.modules.merge_chain import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["skipped"] == "feature_disabled"

    @patch("app.modules.merge_candidates.settings")
    def test_single_merge_chain_of_2_no_chain_created(self, mock_settings):
        """Single merge = 2 vessels = no chain (requires >= 3)."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True
        candidates = [_make_merge_candidate(100, 1, 2, confidence_score=80)]
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        from app.modules.merge_chain import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_created"] == 0

    @patch("app.modules.merge_candidates.settings")
    def test_transitive_merges_single_chain(self, mock_settings):
        """A->B, B->C creates a single chain of 3."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True
        candidates = [
            _make_merge_candidate(100, 1, 2, confidence_score=80,
                                  created_at=datetime.datetime(2025, 1, 1)),
            _make_merge_candidate(101, 2, 3, confidence_score=70,
                                  created_at=datetime.datetime(2025, 2, 1)),
        ]
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        vessel_mocks = {i: MagicMock(vessel_id=i, imo="1234567", mmsi="123456789") for i in range(1, 4)}
        db.query.return_value.get.side_effect = lambda vid: vessel_mocks.get(vid)
        db.query.return_value.filter.return_value.first.return_value = None

        from app.modules.merge_chain import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_created"] == 1

    @patch("app.modules.merge_candidates.settings")
    def test_disconnected_merges_separate_chains(self, mock_settings):
        """Two disconnected 3-vessel groups = two chains."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True
        candidates = [
            _make_merge_candidate(100, 1, 2, confidence_score=80,
                                  created_at=datetime.datetime(2025, 1, 1)),
            _make_merge_candidate(101, 2, 3, confidence_score=70,
                                  created_at=datetime.datetime(2025, 2, 1)),
            _make_merge_candidate(102, 10, 20, confidence_score=90,
                                  created_at=datetime.datetime(2025, 3, 1)),
            _make_merge_candidate(103, 20, 30, confidence_score=85,
                                  created_at=datetime.datetime(2025, 4, 1)),
        ]
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        vessel_mocks = {}
        for vid in [1, 2, 3, 10, 20, 30]:
            vessel_mocks[vid] = MagicMock(vessel_id=vid, imo="1234567", mmsi="123456789")
        db.query.return_value.get.side_effect = lambda vid: vessel_mocks.get(vid)
        db.query.return_value.filter.return_value.first.return_value = None

        from app.modules.merge_chain import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_created"] == 2

    @patch("app.modules.merge_candidates.settings")
    def test_empty_merges_no_chains(self, mock_settings):
        """No merge candidates = no chains."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        from app.modules.merge_chain import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_created"] == 0

    @patch("app.modules.merge_candidates.settings")
    def test_confidence_is_minimum_across_links(self, mock_settings):
        """Chain confidence = min of all link confidence scores."""
        mock_settings.MERGE_CHAIN_DETECTION_ENABLED = True
        candidates = [
            _make_merge_candidate(100, 1, 2, confidence_score=90,
                                  created_at=datetime.datetime(2025, 1, 1)),
            _make_merge_candidate(101, 2, 3, confidence_score=55,
                                  created_at=datetime.datetime(2025, 2, 1)),
        ]
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = candidates
        vessel_mocks = {i: MagicMock(vessel_id=i, imo="1234567", mmsi="123456789") for i in range(1, 4)}
        db.query.return_value.get.side_effect = lambda vid: vessel_mocks.get(vid)
        db.query.return_value.filter.return_value.first.return_value = None

        added_objects = []
        db.add.side_effect = lambda obj: added_objects.append(obj)

        from app.modules.merge_chain import detect_merge_chains
        result = detect_merge_chains(db)
        assert result["chains_created"] == 1
        assert len(added_objects) == 1
        assert added_objects[0].confidence == 55


