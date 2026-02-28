"""Tests for API response consistency and pagination standardization (Task F2).

Covers:
- Merged vessel returns VesselDetailRead with merged_into_vessel_id populated
- Normal vessel returns VesselDetailRead with merged_into_vessel_id = null
- Each paginated endpoint returns {items, total} structure
- Pagination params (skip/limit) work correctly
- Default pagination values work
"""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Merged Vessel Response Consistency
# ---------------------------------------------------------------------------


class TestMergedVesselResponse:
    """Merged and normal vessels should return the same VesselDetailRead schema."""

    def _make_normal_vessel(self, mock_db):
        """Create a normal (non-merged) vessel mock."""
        vessel = MagicMock()
        vessel.vessel_id = 1
        vessel.mmsi = "123456789"
        vessel.imo = "IMO1234567"
        vessel.name = "NORMAL VESSEL"
        vessel.flag = "PA"
        vessel.vessel_type = "Crude Oil Tanker"
        vessel.deadweight = 50000.0
        vessel.year_built = 2005
        vessel.ais_class = MagicMock(value="A")
        vessel.flag_risk_category = MagicMock(value="high")
        vessel.pi_coverage_status = MagicMock(value="unknown")
        vessel.psc_detained_last_12m = False
        vessel.mmsi_first_seen_utc = datetime(2020, 1, 1, tzinfo=timezone.utc)
        vessel.vessel_laid_up_30d = False
        vessel.vessel_laid_up_60d = False
        vessel.vessel_laid_up_in_sts_zone = False
        vessel.merged_into_vessel_id = None
        mock_db.query.return_value.filter.return_value.first.return_value = vessel
        mock_db.query.return_value.filter.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.filter.return_value.all.return_value = []
        return vessel

    def _make_merged_vessel(self, mock_db):
        """Create a merged (absorbed) vessel mock."""
        vessel = MagicMock()
        vessel.vessel_id = 2
        vessel.mmsi = "987654321"
        vessel.imo = None
        vessel.name = "MERGED VESSEL"
        vessel.flag = "LR"
        vessel.vessel_type = "Products Tanker"
        vessel.deadweight = 30000.0
        vessel.year_built = 2010
        vessel.ais_class = MagicMock(value="B")
        vessel.flag_risk_category = MagicMock(value="medium")
        vessel.pi_coverage_status = MagicMock(value="no_coverage")
        vessel.psc_detained_last_12m = False
        vessel.mmsi_first_seen_utc = None
        vessel.vessel_laid_up_30d = False
        vessel.vessel_laid_up_60d = False
        vessel.vessel_laid_up_in_sts_zone = False
        vessel.merged_into_vessel_id = 1  # merged into vessel 1
        mock_db.query.return_value.filter.return_value.first.return_value = vessel
        # resolve_canonical will call .get() on the canonical
        canonical = MagicMock()
        canonical.merged_into_vessel_id = None
        mock_db.query.return_value.get.return_value = canonical
        return vessel

    def test_normal_vessel_returns_vessel_detail_with_null_merged_id(self, api_client, mock_db):
        """Normal vessel returns VesselDetailRead with merged_into_vessel_id = null."""
        self._make_normal_vessel(mock_db)
        resp = api_client.get("/api/v1/vessels/1")
        assert resp.status_code == 200
        data = resp.json()
        # Core fields present
        assert data["vessel_id"] == 1
        assert data["mmsi"] == "123456789"
        assert data["name"] == "NORMAL VESSEL"
        # merged_into_vessel_id is null for normal vessels
        assert data["merged_into_vessel_id"] is None
        # Standard VesselDetailRead fields present
        assert "watchlist_entries" in data
        assert "spoofing_anomalies_30d" in data
        assert "loitering_events_30d" in data
        assert "sts_events_60d" in data
        assert "total_gaps_7d" in data
        assert "total_gaps_30d" in data

    def test_merged_vessel_returns_vessel_detail_with_merged_id(self, api_client, mock_db):
        """Merged vessel returns VesselDetailRead with merged_into_vessel_id populated."""
        self._make_merged_vessel(mock_db)
        resp = api_client.get("/api/v1/vessels/2")
        assert resp.status_code == 200
        data = resp.json()
        # Core fields present -- same shape as normal vessel
        assert data["vessel_id"] == 2
        assert data["mmsi"] == "987654321"
        assert data["name"] == "MERGED VESSEL"
        # merged_into_vessel_id is set for absorbed vessels
        assert data["merged_into_vessel_id"] is not None
        # Standard VesselDetailRead fields present (even if empty)
        assert "watchlist_entries" in data
        assert "spoofing_anomalies_30d" in data
        assert "loitering_events_30d" in data
        assert "sts_events_60d" in data
        assert "total_gaps_7d" in data
        assert "total_gaps_30d" in data

    def test_merged_vessel_has_same_keys_as_normal(self, api_client, mock_db):
        """Both merged and normal vessels return the same set of top-level keys."""
        # Get normal vessel response keys
        self._make_normal_vessel(mock_db)
        normal_resp = api_client.get("/api/v1/vessels/1")
        normal_keys = set(normal_resp.json().keys())

        # Get merged vessel response keys
        self._make_merged_vessel(mock_db)
        merged_resp = api_client.get("/api/v1/vessels/2")
        merged_keys = set(merged_resp.json().keys())

        assert normal_keys == merged_keys, (
            f"Key mismatch: normal_only={normal_keys - merged_keys}, "
            f"merged_only={merged_keys - normal_keys}"
        )

    def test_merged_vessel_no_longer_returns_old_redirect_shape(self, api_client, mock_db):
        """Merged vessel must NOT return the old {merged, canonical_vessel_id, redirect_url} shape."""
        self._make_merged_vessel(mock_db)
        resp = api_client.get("/api/v1/vessels/2")
        data = resp.json()
        # Old fields should not be present
        assert "merged" not in data
        assert "canonical_vessel_id" not in data
        assert "redirect_url" not in data
        assert "absorbed_mmsi" not in data


# ---------------------------------------------------------------------------
# Pagination: Watchlist
# ---------------------------------------------------------------------------


class TestWatchlistPagination:
    """GET /watchlist should return {items, total} envelope with skip/limit."""

    def test_watchlist_returns_items_total_envelope(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/watchlist")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert data["total"] == 0

    def test_watchlist_with_skip_and_limit(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.count.return_value = 100
        mock_db.query.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/watchlist?skip=10&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 100
        assert isinstance(data["items"], list)

    def test_watchlist_default_pagination(self, api_client, mock_db):
        """Default skip=0, limit=50 should work without query params."""
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/watchlist")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    def test_watchlist_negative_skip_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/watchlist?skip=-1")
        assert resp.status_code == 422

    def test_watchlist_limit_over_500_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/watchlist?limit=501")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Pagination: Dark Vessels
# ---------------------------------------------------------------------------


class TestDarkVesselsPagination:
    """GET /dark-vessels should return {items, total} envelope with skip/limit."""

    def test_dark_vessels_returns_items_total_envelope(self, api_client, mock_db):
        mock_db.query.return_value.count.return_value = 0
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/dark-vessels")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert data["total"] == 0

    def test_dark_vessels_with_skip_and_limit(self, api_client, mock_db):
        mock_db.query.return_value.count.return_value = 75
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/dark-vessels?skip=20&limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 75

    def test_dark_vessels_default_pagination(self, api_client, mock_db):
        """Default skip=0, limit=50 should work without query params."""
        mock_db.query.return_value.count.return_value = 0
        mock_db.query.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/dark-vessels")
        assert resp.status_code == 200

    def test_dark_vessels_negative_skip_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/dark-vessels?skip=-1")
        assert resp.status_code == 422

    def test_dark_vessels_limit_over_500_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/dark-vessels?limit=501")
        assert resp.status_code == 422

    def test_dark_vessels_with_filter_returns_envelope(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.count.return_value = 3
        mock_db.query.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/dark-vessels?ais_match_result=unmatched")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data


# ---------------------------------------------------------------------------
# Pagination: Hunt Mission Candidates
# ---------------------------------------------------------------------------


class TestHuntCandidatesPagination:
    """GET /hunt/missions/{id}/candidates should return {items, total} envelope."""

    def test_candidates_returns_items_total_envelope(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/hunt/missions/1/candidates")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert data["total"] == 0

    def test_candidates_with_skip_and_limit(self, api_client, mock_db):
        mock_db.query.return_value.filter.return_value.count.return_value = 25
        mock_db.query.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/hunt/missions/1/candidates?skip=5&limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 25

    def test_candidates_default_pagination(self, api_client, mock_db):
        """Default skip=0, limit=50 should work without query params."""
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        mock_db.query.return_value.filter.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/hunt/missions/1/candidates")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data

    def test_candidates_negative_skip_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/hunt/missions/1/candidates?skip=-1")
        assert resp.status_code == 422

    def test_candidates_limit_over_500_returns_422(self, api_client, mock_db):
        resp = api_client.get("/api/v1/hunt/missions/1/candidates?limit=501")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Pagination: STS Events (already paginated, verify consistency)
# ---------------------------------------------------------------------------


class TestStsEventsPagination:
    """GET /sts-events already returns {items, total} -- verify it matches the pattern."""

    def test_sts_events_returns_items_total_envelope(self, api_client, mock_db):
        mock_db.query.return_value.order_by.return_value.count.return_value = 0
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/sts-events")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)

    def test_sts_events_with_skip_and_limit(self, api_client, mock_db):
        mock_db.query.return_value.order_by.return_value.count.return_value = 50
        mock_db.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
        resp = api_client.get("/api/v1/sts-events?skip=10&limit=20")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 50
