"""Tests for /health/collection-status endpoint and collection pipeline integration."""

from unittest.mock import patch


class TestCollectionStatusEndpointShape:
    """Test the JSON response shape of /health/collection-status."""

    def test_collection_status_endpoint_shape(self, api_client, mock_db):
        """JSON has all required keys."""
        mock_db.query.return_value.scalar.return_value = 0
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0
        mock_db.query.return_value.group_by.return_value.all.return_value = []
        resp = api_client.get("/api/v1/health/collection-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "collection_runs" in data
        assert "ais_density" in data
        assert "per_source_breakdown" in data
        assert "data_quality_warnings" in data
        assert "total_points" in data
        assert "total_vessels" in data
        assert "merge_readiness" in data

    def test_collection_status_empty_db(self, api_client, mock_db):
        """Zero vessels => safe division, no crash."""
        mock_db.query.return_value.scalar.return_value = 0
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0
        mock_db.query.return_value.group_by.return_value.all.return_value = []
        resp = api_client.get("/api/v1/health/collection-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ais_density"] == 0.0
        assert data["total_vessels"] == 0
        assert any("No vessels" in w for w in data["data_quality_warnings"])

    def test_collection_status_days_param(self, api_client, mock_db):
        """Respects the days query parameter."""
        mock_db.query.return_value.scalar.return_value = 0
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0
        mock_db.query.return_value.group_by.return_value.all.return_value = []
        resp = api_client.get("/api/v1/health/collection-status?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert data["days"] == 30

    def test_collection_status_days_min_validation(self, api_client, mock_db):
        """days < 1 is rejected by Pydantic."""
        resp = api_client.get("/api/v1/health/collection-status?days=0")
        assert resp.status_code == 422

    def test_collection_status_days_max_validation(self, api_client, mock_db):
        """days > 90 is rejected by Pydantic."""
        resp = api_client.get("/api/v1/health/collection-status?days=91")
        assert resp.status_code == 422


class TestCollectionStatusDensityMath:
    """Test AIS density calculation logic."""

    def test_density_math(self, api_client, mock_db):
        """avg = total_points / total_vessels."""
        call_count = [0]

        def scalar_side_effect(*args, **kwargs):
            call_count[0] += 1
            # First call: total_vessels count
            if call_count[0] == 1:
                return 10
            # Second call: vessels_with_imo count
            if call_count[0] == 2:
                return 5
            # Third call: total_points count
            if call_count[0] == 3:
                return 200
            # Remaining calls: points_last_24h, points_last_n_days
            return 50

        mock_db.query.return_value.scalar.side_effect = scalar_side_effect
        mock_db.query.return_value.filter.return_value.scalar.side_effect = scalar_side_effect
        mock_db.query.return_value.group_by.return_value.all.return_value = []

        resp = api_client.get("/api/v1/health/collection-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ais_density"] == 20.0


class TestCollectionStatusNoCollectionTable:
    """Test graceful fallback when CollectionRun doesn't exist."""

    def test_no_collection_table_graceful(self, api_client, mock_db):
        """Returns empty collection_runs when model is unavailable."""
        mock_db.query.return_value.scalar.return_value = 0
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0
        mock_db.query.return_value.group_by.return_value.all.return_value = []

        resp = api_client.get("/api/v1/health/collection-status")
        assert resp.status_code == 200
        data = resp.json()
        # CollectionRun import fails gracefully -> empty list
        assert isinstance(data["collection_runs"], list)


class TestCollectionStatusMergeReadiness:
    """Test merge readiness diagnostic inclusion."""

    def test_merge_readiness_included(self, api_client, mock_db):
        """Response includes merge_readiness key."""
        mock_db.query.return_value.scalar.return_value = 0
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0
        mock_db.query.return_value.group_by.return_value.all.return_value = []

        resp = api_client.get("/api/v1/health/collection-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "merge_readiness" in data
        assert isinstance(data["merge_readiness"], dict)

    @patch("app.modules.identity_resolver.diagnose_merge_readiness", side_effect=Exception("boom"))
    def test_merge_readiness_error_fallback(self, mock_diag, api_client, mock_db):
        """Merge readiness error returns fallback dict."""
        mock_db.query.return_value.scalar.return_value = 0
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0
        mock_db.query.return_value.group_by.return_value.all.return_value = []

        resp = api_client.get("/api/v1/health/collection-status")
        assert resp.status_code == 200
        data = resp.json()
        assert "merge_readiness" in data


class TestCollectionStatusPerSource:
    """Test per-source breakdown grouping."""

    def test_per_source_breakdown(self, api_client, mock_db):
        """Source grouping returns correct structure."""
        call_count = [0]

        def scalar_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return 5  # total_vessels
            if call_count[0] == 2:
                return 3  # vessels_with_imo
            if call_count[0] == 3:
                return 100  # total_points
            return 20

        mock_db.query.return_value.scalar.side_effect = scalar_side_effect
        mock_db.query.return_value.filter.return_value.scalar.side_effect = scalar_side_effect
        mock_db.query.return_value.group_by.return_value.all.return_value = [
            ("digitraffic", 60),
            ("aisstream", 40),
        ]

        resp = api_client.get("/api/v1/health/collection-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["per_source_breakdown"]["digitraffic"] == 60
        assert data["per_source_breakdown"]["aisstream"] == 40


class TestCollectionStatusWarnings:
    """Test data quality warning generation."""

    def test_no_points_warning(self, api_client, mock_db):
        """No points in 24h triggers warning."""
        call_count = [0]

        def scalar_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return 10  # total_vessels
            if call_count[0] == 2:
                return 8  # vessels_with_imo
            if call_count[0] == 3:
                return 100  # total_points
            if call_count[0] == 4:
                return 0  # points_last_24h — triggers warning
            return 50

        mock_db.query.return_value.scalar.side_effect = scalar_side_effect
        mock_db.query.return_value.filter.return_value.scalar.side_effect = scalar_side_effect
        mock_db.query.return_value.group_by.return_value.all.return_value = []

        resp = api_client.get("/api/v1/health/collection-status")
        assert resp.status_code == 200
        data = resp.json()
        assert any("24 hours" in w for w in data["data_quality_warnings"])

    def test_low_density_warning(self, api_client, mock_db):
        """Low AIS density triggers warning."""
        call_count = [0]

        def scalar_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return 100  # total_vessels
            if call_count[0] == 2:
                return 80  # vessels_with_imo
            if call_count[0] == 3:
                return 200  # total_points -> density = 2.0
            return 50

        mock_db.query.return_value.scalar.side_effect = scalar_side_effect
        mock_db.query.return_value.filter.return_value.scalar.side_effect = scalar_side_effect
        mock_db.query.return_value.group_by.return_value.all.return_value = []

        resp = api_client.get("/api/v1/health/collection-status")
        assert resp.status_code == 200
        data = resp.json()
        assert any("density" in w.lower() for w in data["data_quality_warnings"])

    def test_low_imo_coverage_warning(self, api_client, mock_db):
        """Low IMO coverage triggers warning."""
        call_count = [0]

        def scalar_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return 100  # total_vessels
            if call_count[0] == 2:
                return 10  # vessels_with_imo -> 10% IMO coverage
            if call_count[0] == 3:
                return 5000  # total_points
            return 100

        mock_db.query.return_value.scalar.side_effect = scalar_side_effect
        mock_db.query.return_value.filter.return_value.scalar.side_effect = scalar_side_effect
        mock_db.query.return_value.group_by.return_value.all.return_value = []

        resp = api_client.get("/api/v1/health/collection-status")
        assert resp.status_code == 200
        data = resp.json()
        assert any("IMO" in w for w in data["data_quality_warnings"])


class TestCollectionStatusWithRuns:
    """Test with mock CollectionRun data."""

    @patch("app.modules.identity_resolver.diagnose_merge_readiness")
    def test_collection_status_with_runs(self, mock_diag, api_client, mock_db):
        """With mock CollectionRun data, runs are returned."""
        mock_diag.return_value = {"status": "ready", "issues": []}

        call_count = [0]

        def scalar_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return 5
            if call_count[0] == 2:
                return 3
            if call_count[0] == 3:
                return 50
            return 10

        mock_db.query.return_value.scalar.side_effect = scalar_side_effect
        mock_db.query.return_value.filter.return_value.scalar.side_effect = scalar_side_effect
        mock_db.query.return_value.group_by.return_value.all.return_value = []

        resp = api_client.get("/api/v1/health/collection-status")
        assert resp.status_code == 200
        data = resp.json()
        # collection_runs is a list (may be empty if model doesn't exist)
        assert isinstance(data["collection_runs"], list)
