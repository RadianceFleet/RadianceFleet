"""Tests for vessel similarity tool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.models.owner_cluster_member import OwnerClusterMember
from app.models.vessel_owner import VesselOwner
from app.models.vessel_similarity_result import VesselSimilarityResult
from app.modules.vessel_similarity import (
    _distance_band,
    _normalise_distance,
    _similarity_tier,
    compute_composite_similarity,
    compute_ownership_similarity,
    find_similar_vessels,
    persist_similarity_results,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_owner(
    owner_id: int,
    vessel_id: int,
    owner_name: str = "Acme Shipping",
    country: str | None = None,
    ism_manager: str | None = None,
    pi_club_name: str | None = None,
) -> MagicMock:
    o = MagicMock(spec=VesselOwner)
    o.owner_id = owner_id
    o.vessel_id = vessel_id
    o.owner_name = owner_name
    o.country = country
    o.ism_manager = ism_manager
    o.pi_club_name = pi_club_name
    o.is_sanctioned = False
    return o


def _make_cluster_member(member_id: int, cluster_id: int, owner_id: int) -> MagicMock:
    m = MagicMock(spec=OwnerClusterMember)
    m.member_id = member_id
    m.cluster_id = cluster_id
    m.owner_id = owner_id
    return m


def _make_fingerprint(vessel_id: int, feature_vector: dict | None = None):
    fp = MagicMock()
    fp.vessel_id = vessel_id
    fp.feature_vector_json = feature_vector or {
        "cruise_speed_median": 12.0,
        "cruise_speed_iqr": 2.0,
        "sog_max": 15.0,
        "acceleration_profile": 0.5,
        "turn_rate_median": 1.0,
        "heading_stability": 3.0,
        "draught_range": 1.5,
        "tx_interval_median": 30.0,
        "tx_interval_var": 100.0,
        "deceleration_profile": -0.3,
    }
    fp.covariance_json = [[1.0 if i == j else 0.0 for j in range(10)] for i in range(10)]
    fp.is_diagonal_only = True
    fp.sample_count = 20
    return fp


# ---------------------------------------------------------------------------
# Distance normalisation
# ---------------------------------------------------------------------------


class TestNormaliseDistance:
    def test_zero_distance_returns_one(self):
        assert _normalise_distance(0.0) == 1.0

    def test_scale_distance_returns_half(self):
        # 1/(1 + 10/10) = 0.5
        assert abs(_normalise_distance(10.0) - 0.5) < 1e-9

    def test_large_distance_approaches_zero(self):
        assert _normalise_distance(1000.0) < 0.02

    def test_monotonically_decreasing(self):
        prev = 1.0
        for d in [1.0, 5.0, 10.0, 50.0]:
            val = _normalise_distance(d)
            assert val < prev
            prev = val


# ---------------------------------------------------------------------------
# Distance band
# ---------------------------------------------------------------------------


class TestDistanceBand:
    def test_near_band(self):
        assert _distance_band(2.0) == "near"

    def test_near_boundary(self):
        assert _distance_band(3.0) == "near"

    def test_moderate_band(self):
        assert _distance_band(4.0) == "moderate"

    def test_moderate_boundary(self):
        assert _distance_band(6.0) == "moderate"

    def test_far_band(self):
        assert _distance_band(7.0) == "far"


# ---------------------------------------------------------------------------
# Similarity tier
# ---------------------------------------------------------------------------


class TestSimilarityTier:
    def test_high_tier(self):
        assert _similarity_tier(0.8) == "HIGH"

    def test_high_boundary(self):
        assert _similarity_tier(0.7) == "HIGH"

    def test_medium_tier(self):
        assert _similarity_tier(0.5) == "MEDIUM"

    def test_medium_boundary(self):
        assert _similarity_tier(0.4) == "MEDIUM"

    def test_low_tier(self):
        assert _similarity_tier(0.3) == "LOW"

    def test_zero_is_low(self):
        assert _similarity_tier(0.0) == "LOW"


# ---------------------------------------------------------------------------
# Ownership similarity
# ---------------------------------------------------------------------------


class TestOwnershipSimilarity:
    def test_no_owners_returns_zero(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        result = compute_ownership_similarity(db, 1, 2)
        assert result["score"] == 0.0

    def test_shared_cluster(self):
        db = MagicMock()
        owner_a = _make_owner(10, 1, "Alpha Ltd")
        owner_b = _make_owner(20, 2, "Beta Ltd")

        # Query for owners: first call returns owner_a, second returns owner_b
        # Query for cluster members: third and fourth calls
        call_count = {"n": 0}

        def side_effect_filter(*args, **kwargs):
            mock = MagicMock()
            call_count["n"] += 1
            n = call_count["n"]
            if n == 1:
                mock.all.return_value = [owner_a]
            elif n == 2:
                mock.all.return_value = [owner_b]
            elif n == 3:
                mock.all.return_value = [_make_cluster_member(1, 100, 10)]
            elif n == 4:
                mock.all.return_value = [_make_cluster_member(2, 100, 20)]
            else:
                mock.all.return_value = []
            return mock

        db.query.return_value.filter.side_effect = side_effect_filter
        result = compute_ownership_similarity(db, 1, 2)
        assert result["breakdown"]["shared_cluster"] is True
        assert result["score"] > 0

    def test_shared_ism_manager(self):
        db = MagicMock()
        owner_a = _make_owner(10, 1, "Alpha", ism_manager="GlobalISM Co")
        owner_b = _make_owner(20, 2, "Beta", ism_manager="GlobalISM Co")

        call_count = {"n": 0}

        def side_effect_filter(*args, **kwargs):
            mock = MagicMock()
            call_count["n"] += 1
            n = call_count["n"]
            if n == 1:
                mock.all.return_value = [owner_a]
            elif n == 2:
                mock.all.return_value = [owner_b]
            else:
                mock.all.return_value = []
            return mock

        db.query.return_value.filter.side_effect = side_effect_filter
        result = compute_ownership_similarity(db, 1, 2)
        assert result["breakdown"]["shared_ism_manager"] is True
        assert result["score"] >= 0.20

    def test_shared_pi_club(self):
        db = MagicMock()
        owner_a = _make_owner(10, 1, "Alpha", pi_club_name="Gard P&I")
        owner_b = _make_owner(20, 2, "Beta", pi_club_name="Gard P&I")

        call_count = {"n": 0}

        def side_effect_filter(*args, **kwargs):
            mock = MagicMock()
            call_count["n"] += 1
            n = call_count["n"]
            if n == 1:
                mock.all.return_value = [owner_a]
            elif n == 2:
                mock.all.return_value = [owner_b]
            else:
                mock.all.return_value = []
            return mock

        db.query.return_value.filter.side_effect = side_effect_filter
        result = compute_ownership_similarity(db, 1, 2)
        assert result["breakdown"]["shared_pi_club"] is True
        assert result["score"] >= 0.15

    def test_same_owner_name(self):
        db = MagicMock()
        owner_a = _make_owner(10, 1, "Acme Shipping Ltd")
        owner_b = _make_owner(20, 2, "Acme Shipping Ltd")

        call_count = {"n": 0}

        def side_effect_filter(*args, **kwargs):
            mock = MagicMock()
            call_count["n"] += 1
            n = call_count["n"]
            if n == 1:
                mock.all.return_value = [owner_a]
            elif n == 2:
                mock.all.return_value = [owner_b]
            else:
                mock.all.return_value = []
            return mock

        db.query.return_value.filter.side_effect = side_effect_filter
        result = compute_ownership_similarity(db, 1, 2)
        assert result["breakdown"]["same_owner_name"] is True
        assert result["score"] >= 0.20

    def test_same_country(self):
        db = MagicMock()
        owner_a = _make_owner(10, 1, "Alpha", country="PA")
        owner_b = _make_owner(20, 2, "Beta", country="PA")

        call_count = {"n": 0}

        def side_effect_filter(*args, **kwargs):
            mock = MagicMock()
            call_count["n"] += 1
            n = call_count["n"]
            if n == 1:
                mock.all.return_value = [owner_a]
            elif n == 2:
                mock.all.return_value = [owner_b]
            else:
                mock.all.return_value = []
            return mock

        db.query.return_value.filter.side_effect = side_effect_filter
        result = compute_ownership_similarity(db, 1, 2)
        assert result["breakdown"]["same_country"] is True
        assert result["score"] >= 0.10

    def test_all_signals_match_gives_full_score(self):
        db = MagicMock()
        owner_a = _make_owner(
            10, 1, "Acme Shipping", country="PA", ism_manager="ISM Co", pi_club_name="Gard"
        )
        owner_b = _make_owner(
            20, 2, "Acme Shipping", country="PA", ism_manager="ISM Co", pi_club_name="Gard"
        )

        call_count = {"n": 0}

        def side_effect_filter(*args, **kwargs):
            mock = MagicMock()
            call_count["n"] += 1
            n = call_count["n"]
            if n == 1:
                mock.all.return_value = [owner_a]
            elif n == 2:
                mock.all.return_value = [owner_b]
            elif n == 3:
                mock.all.return_value = [_make_cluster_member(1, 100, 10)]
            elif n == 4:
                mock.all.return_value = [_make_cluster_member(2, 100, 20)]
            else:
                mock.all.return_value = []
            return mock

        db.query.return_value.filter.side_effect = side_effect_filter
        result = compute_ownership_similarity(db, 1, 2)
        assert result["score"] == 1.0

    def test_no_match_gives_zero(self):
        db = MagicMock()
        owner_a = _make_owner(10, 1, "Alpha Corp", country="PA")
        owner_b = _make_owner(20, 2, "Beta Corp", country="LR")

        call_count = {"n": 0}

        def side_effect_filter(*args, **kwargs):
            mock = MagicMock()
            call_count["n"] += 1
            n = call_count["n"]
            if n == 1:
                mock.all.return_value = [owner_a]
            elif n == 2:
                mock.all.return_value = [owner_b]
            else:
                mock.all.return_value = []
            return mock

        db.query.return_value.filter.side_effect = side_effect_filter
        result = compute_ownership_similarity(db, 1, 2)
        assert result["score"] == 0.0


# ---------------------------------------------------------------------------
# Composite similarity
# ---------------------------------------------------------------------------


class TestCompositeSimilarity:
    @patch("app.modules.vessel_similarity.compute_ownership_similarity")
    @patch("app.modules.vessel_fingerprint.mahalanobis_distance")
    def test_default_weights(self, mock_maha, mock_own):
        db = MagicMock()
        fp1 = _make_fingerprint(1)
        fp2 = _make_fingerprint(2)

        db.query.return_value.filter.return_value.first.side_effect = [fp1, fp2]
        mock_maha.return_value = 0.0  # perfect match -> similarity=1.0
        mock_own.return_value = {"score": 1.0, "breakdown": {}}

        result = compute_composite_similarity(db, 1, 2)
        assert result is not None
        # 0.6 * 1.0 + 0.4 * 1.0 = 1.0
        assert result["composite_similarity_score"] == 1.0
        assert result["similarity_tier"] == "HIGH"

    @patch("app.modules.vessel_similarity.compute_ownership_similarity")
    @patch("app.modules.vessel_fingerprint.mahalanobis_distance")
    def test_fingerprint_only(self, mock_maha, mock_own):
        db = MagicMock()
        fp1 = _make_fingerprint(1)
        fp2 = _make_fingerprint(2)

        db.query.return_value.filter.return_value.first.side_effect = [fp1, fp2]
        mock_maha.return_value = 0.0
        mock_own.return_value = {"score": 0.0, "breakdown": {}}

        result = compute_composite_similarity(db, 1, 2, include_ownership=False)
        assert result is not None
        # 0.6 * 1.0 + 0.4 * 0.0 = 0.6
        assert result["composite_similarity_score"] == 0.6

    @patch("app.modules.vessel_similarity.compute_ownership_similarity")
    @patch("app.modules.vessel_fingerprint.mahalanobis_distance")
    def test_medium_tier(self, mock_maha, mock_own):
        db = MagicMock()
        fp1 = _make_fingerprint(1)
        fp2 = _make_fingerprint(2)

        db.query.return_value.filter.return_value.first.side_effect = [fp1, fp2]
        # distance=10 -> similarity=0.5
        mock_maha.return_value = 10.0
        mock_own.return_value = {"score": 0.25, "breakdown": {}}

        result = compute_composite_similarity(db, 1, 2)
        assert result is not None
        # 0.6 * 0.5 + 0.4 * 0.25 = 0.3 + 0.1 = 0.4
        assert result["similarity_tier"] == "MEDIUM"

    @patch("app.modules.vessel_fingerprint.mahalanobis_distance")
    def test_no_fingerprint_returns_none(self, mock_maha):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        result = compute_composite_similarity(db, 1, 2)
        assert result is None

    @patch("app.modules.vessel_similarity.compute_ownership_similarity")
    @patch("app.modules.vessel_fingerprint.mahalanobis_distance")
    def test_distance_band_included(self, mock_maha, mock_own):
        db = MagicMock()
        fp1 = _make_fingerprint(1)
        fp2 = _make_fingerprint(2)

        db.query.return_value.filter.return_value.first.side_effect = [fp1, fp2]
        mock_maha.return_value = 4.0
        mock_own.return_value = {"score": 0.0, "breakdown": {}}

        result = compute_composite_similarity(db, 1, 2)
        assert result is not None
        assert result["fingerprint_band"] == "moderate"

    @patch("app.modules.vessel_similarity.settings")
    @patch("app.modules.vessel_similarity.compute_ownership_similarity")
    @patch("app.modules.vessel_fingerprint.mahalanobis_distance")
    def test_custom_weights(self, mock_maha, mock_own, mock_settings):
        db = MagicMock()
        fp1 = _make_fingerprint(1)
        fp2 = _make_fingerprint(2)

        db.query.return_value.filter.return_value.first.side_effect = [fp1, fp2]
        mock_maha.return_value = 0.0
        mock_own.return_value = {"score": 0.0, "breakdown": {}}
        mock_settings.VESSEL_SIMILARITY_FINGERPRINT_WEIGHT = 0.8
        mock_settings.VESSEL_SIMILARITY_OWNERSHIP_WEIGHT = 0.2

        result = compute_composite_similarity(db, 1, 2)
        assert result is not None
        # 0.8 * 1.0 + 0.2 * 0.0 = 0.8
        assert result["composite_similarity_score"] == 0.8


# ---------------------------------------------------------------------------
# find_similar_vessels
# ---------------------------------------------------------------------------


class TestFindSimilarVessels:
    @patch("app.modules.vessel_similarity.settings")
    def test_disabled_returns_empty(self, mock_settings):
        mock_settings.VESSEL_SIMILARITY_ENABLED = False
        db = MagicMock()
        result = find_similar_vessels(db, 1)
        assert result == []

    @patch("app.modules.vessel_similarity.compute_composite_similarity")
    @patch("app.modules.vessel_fingerprint.rank_candidates")
    @patch("app.modules.vessel_similarity.settings")
    def test_returns_sorted_results(self, mock_settings, mock_rank, mock_comp):
        mock_settings.VESSEL_SIMILARITY_ENABLED = True
        db = MagicMock()
        mock_rank.return_value = [
            {"vessel_id": 10, "distance": 2.0, "band": "CLOSE"},
            {"vessel_id": 20, "distance": 5.0, "band": "SIMILAR"},
        ]
        mock_comp.side_effect = [
            {
                "source_vessel_id": 1,
                "target_vessel_id": 10,
                "composite_similarity_score": 0.5,
                "fingerprint_distance": 2.0,
                "fingerprint_band": "near",
                "ownership_similarity_score": 0.3,
            },
            {
                "source_vessel_id": 1,
                "target_vessel_id": 20,
                "composite_similarity_score": 0.8,
                "fingerprint_distance": 5.0,
                "fingerprint_band": "moderate",
                "ownership_similarity_score": 0.9,
            },
        ]

        result = find_similar_vessels(db, 1, limit=10)
        assert len(result) == 2
        # Should be sorted descending by composite score
        assert result[0]["target_vessel_id"] == 20
        assert result[1]["target_vessel_id"] == 10

    @patch("app.modules.vessel_fingerprint.rank_candidates")
    @patch("app.modules.vessel_similarity.settings")
    def test_no_candidates_returns_empty(self, mock_settings, mock_rank):
        mock_settings.VESSEL_SIMILARITY_ENABLED = True
        db = MagicMock()
        mock_rank.return_value = []

        result = find_similar_vessels(db, 1)
        assert result == []

    @patch("app.modules.vessel_similarity.compute_composite_similarity")
    @patch("app.modules.vessel_fingerprint.rank_candidates")
    @patch("app.modules.vessel_similarity.settings")
    def test_respects_limit(self, mock_settings, mock_rank, mock_comp):
        mock_settings.VESSEL_SIMILARITY_ENABLED = True
        db = MagicMock()
        mock_rank.return_value = [
            {"vessel_id": i, "distance": float(i), "band": "CLOSE"}
            for i in range(10, 15)
        ]
        mock_comp.side_effect = [
            {
                "source_vessel_id": 1,
                "target_vessel_id": i,
                "composite_similarity_score": 1.0 - i * 0.01,
                "fingerprint_distance": float(i),
                "fingerprint_band": "near",
                "ownership_similarity_score": 0.0,
            }
            for i in range(10, 15)
        ]

        result = find_similar_vessels(db, 1, limit=2)
        assert len(result) == 2

    @patch("app.modules.vessel_similarity.compute_composite_similarity")
    @patch("app.modules.vessel_fingerprint.rank_candidates")
    @patch("app.modules.vessel_similarity.settings")
    def test_skips_none_composites(self, mock_settings, mock_rank, mock_comp):
        mock_settings.VESSEL_SIMILARITY_ENABLED = True
        db = MagicMock()
        mock_rank.return_value = [
            {"vessel_id": 10, "distance": 2.0, "band": "CLOSE"},
            {"vessel_id": 20, "distance": 5.0, "band": "SIMILAR"},
        ]
        # First candidate has no fingerprint
        mock_comp.side_effect = [
            None,
            {
                "source_vessel_id": 1,
                "target_vessel_id": 20,
                "composite_similarity_score": 0.5,
                "fingerprint_distance": 5.0,
                "fingerprint_band": "moderate",
                "ownership_similarity_score": 0.0,
            },
        ]

        result = find_similar_vessels(db, 1)
        assert len(result) == 1
        assert result[0]["target_vessel_id"] == 20


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistResults:
    def test_creates_new_records(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        results = [
            {
                "source_vessel_id": 1,
                "target_vessel_id": 10,
                "fingerprint_distance": 2.5,
                "fingerprint_band": "near",
                "ownership_similarity_score": 0.5,
                "composite_similarity_score": 0.7,
                "similarity_tier": "HIGH",
                "fingerprint_similarity": 0.8,
                "ownership_breakdown": {"shared_cluster": True},
            }
        ]
        records = persist_similarity_results(db, results)
        assert len(records) == 1
        db.add.assert_called_once()
        db.flush.assert_called_once()

    def test_updates_existing_records(self):
        existing = MagicMock(spec=VesselSimilarityResult)
        existing.source_vessel_id = 1
        existing.target_vessel_id = 10

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = existing

        results = [
            {
                "source_vessel_id": 1,
                "target_vessel_id": 10,
                "fingerprint_distance": 3.0,
                "fingerprint_band": "moderate",
                "ownership_similarity_score": 0.4,
                "composite_similarity_score": 0.5,
                "similarity_tier": "MEDIUM",
                "fingerprint_similarity": 0.75,
                "ownership_breakdown": {},
            }
        ]
        records = persist_similarity_results(db, results)
        assert len(records) == 1
        assert existing.fingerprint_distance == 3.0
        assert existing.similarity_tier == "MEDIUM"
        # Should NOT call db.add for existing records
        db.add.assert_not_called()

    def test_empty_results_returns_empty(self):
        db = MagicMock()
        records = persist_similarity_results(db, [])
        assert records == []
        db.flush.assert_called_once()


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class TestVesselSimilarityResultModel:
    def test_model_tablename(self):
        assert VesselSimilarityResult.__tablename__ == "vessel_similarity_results"

    def test_model_has_unique_constraint(self):
        constraints = [
            c.name
            for c in VesselSimilarityResult.__table__.constraints
            if hasattr(c, "name") and c.name
        ]
        assert "uq_similarity_pair" in constraints


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_ownership_similarity_single_owner_vessel_a(self):
        """Vessel A has owners, vessel B has none."""
        db = MagicMock()
        owner_a = _make_owner(10, 1, "Alpha")

        call_count = {"n": 0}

        def side_effect_filter(*args, **kwargs):
            mock = MagicMock()
            call_count["n"] += 1
            if call_count["n"] == 1:
                mock.all.return_value = [owner_a]
            else:
                mock.all.return_value = []
            return mock

        db.query.return_value.filter.side_effect = side_effect_filter
        result = compute_ownership_similarity(db, 1, 2)
        assert result["score"] == 0.0

    def test_ownership_case_insensitive_ism(self):
        """ISM manager comparison should be case-insensitive."""
        db = MagicMock()
        owner_a = _make_owner(10, 1, "Alpha", ism_manager="Global ISM")
        owner_b = _make_owner(20, 2, "Beta", ism_manager="global ism")

        call_count = {"n": 0}

        def side_effect_filter(*args, **kwargs):
            mock = MagicMock()
            call_count["n"] += 1
            if call_count["n"] == 1:
                mock.all.return_value = [owner_a]
            elif call_count["n"] == 2:
                mock.all.return_value = [owner_b]
            else:
                mock.all.return_value = []
            return mock

        db.query.return_value.filter.side_effect = side_effect_filter
        result = compute_ownership_similarity(db, 1, 2)
        assert result["breakdown"]["shared_ism_manager"] is True

    def test_ownership_country_case_insensitive(self):
        """Country comparison should be case-insensitive."""
        db = MagicMock()
        owner_a = _make_owner(10, 1, "Alpha", country="pa")
        owner_b = _make_owner(20, 2, "Beta", country="PA")

        call_count = {"n": 0}

        def side_effect_filter(*args, **kwargs):
            mock = MagicMock()
            call_count["n"] += 1
            if call_count["n"] == 1:
                mock.all.return_value = [owner_a]
            elif call_count["n"] == 2:
                mock.all.return_value = [owner_b]
            else:
                mock.all.return_value = []
            return mock

        db.query.return_value.filter.side_effect = side_effect_filter
        result = compute_ownership_similarity(db, 1, 2)
        assert result["breakdown"]["same_country"] is True
