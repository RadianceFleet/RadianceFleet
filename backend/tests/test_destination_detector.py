"""Tests for AIS destination manipulation detector."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# ── Tests: pure functions ────────────────────────────────────────────


class TestInitialBearing:
    def test_north(self):
        from app.modules.destination_detector import _initial_bearing

        bearing = _initial_bearing(0.0, 0.0, 1.0, 0.0)
        assert abs(bearing - 0.0) < 1.0

    def test_east(self):
        from app.modules.destination_detector import _initial_bearing

        bearing = _initial_bearing(0.0, 0.0, 0.0, 1.0)
        assert abs(bearing - 90.0) < 1.0

    def test_south(self):
        from app.modules.destination_detector import _initial_bearing

        bearing = _initial_bearing(1.0, 0.0, 0.0, 0.0)
        assert abs(bearing - 180.0) < 1.0

    def test_west(self):
        from app.modules.destination_detector import _initial_bearing

        bearing = _initial_bearing(0.0, 1.0, 0.0, 0.0)
        assert abs(bearing - 270.0) < 1.0


class TestBearingDiff:
    def test_same_bearing(self):
        from app.modules.destination_detector import _bearing_diff

        assert _bearing_diff(90.0, 90.0) == 0.0

    def test_opposite_bearing(self):
        from app.modules.destination_detector import _bearing_diff

        assert _bearing_diff(0.0, 180.0) == 180.0

    def test_wraparound(self):
        from app.modules.destination_detector import _bearing_diff

        assert _bearing_diff(350.0, 10.0) == 20.0

    def test_small_difference(self):
        from app.modules.destination_detector import _bearing_diff

        assert abs(_bearing_diff(45.0, 50.0) - 5.0) < 0.01


class TestIsBlankOrGeneric:
    def test_blank_string(self):
        from app.modules.destination_detector import _is_blank_or_generic

        assert _is_blank_or_generic("") is True

    def test_for_orders(self):
        from app.modules.destination_detector import _is_blank_or_generic

        assert _is_blank_or_generic("FOR ORDERS") is True
        assert _is_blank_or_generic("for orders") is True

    def test_tba(self):
        from app.modules.destination_detector import _is_blank_or_generic

        assert _is_blank_or_generic("TBA") is True

    def test_none_returns_false(self):
        from app.modules.destination_detector import _is_blank_or_generic

        assert _is_blank_or_generic(None) is False

    def test_real_port(self):
        from app.modules.destination_detector import _is_blank_or_generic

        assert _is_blank_or_generic("ROTTERDAM") is False

    def test_at_sea(self):
        from app.modules.destination_detector import _is_blank_or_generic

        assert _is_blank_or_generic("AT SEA") is True

    def test_na(self):
        from app.modules.destination_detector import _is_blank_or_generic

        assert _is_blank_or_generic("N/A") is True

    def test_whitespace_stripped(self):
        from app.modules.destination_detector import _is_blank_or_generic

        assert _is_blank_or_generic("  TBA  ") is True

    def test_sts(self):
        from app.modules.destination_detector import _is_blank_or_generic

        assert _is_blank_or_generic("STS") is True


# ── Tests: detect_destination_anomalies ──────────────────────────────


class TestDetectDestinationAnomalies:
    def test_disabled_returns_status(self):
        from app.modules.destination_detector import detect_destination_anomalies

        db = MagicMock()
        with patch("app.modules.destination_detector.settings") as mock_settings:
            mock_settings.DESTINATION_DETECTION_ENABLED = False
            result = detect_destination_anomalies(db)
            assert result["status"] == "disabled"
            assert result["anomalies_created"] == 0

    def test_skips_small_vessels(self):
        from app.modules.destination_detector import detect_destination_anomalies

        db = MagicMock()
        with patch("app.modules.destination_detector.settings") as mock_settings:
            mock_settings.DESTINATION_DETECTION_ENABLED = True
            # No vessels with DWT > 5000
            db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
            db.query.return_value.filter.return_value.all.return_value = []

            result = detect_destination_anomalies(db)
            assert result["anomalies_created"] == 0


class TestGetCorridorCenters:
    def test_returns_corridor_centroids(self):
        from app.modules.destination_detector import _get_corridor_centers

        db = MagicMock()
        corridor = MagicMock()
        corridor.corridor_id = 1
        corridor.name = "Test STS"
        corridor.geometry = "POLYGON((50 20, 60 20, 60 30, 50 30, 50 20))"

        db.query.return_value.filter.return_value.all.return_value = [corridor]

        mock_geom = MagicMock()
        mock_centroid = MagicMock()
        mock_centroid.y = 25.0
        mock_centroid.x = 55.0
        mock_geom.centroid = mock_centroid

        with patch("app.utils.geo.load_geometry", return_value=mock_geom):
            result = _get_corridor_centers(db)
            assert len(result) == 1
            assert result[0]["lat"] == 25.0
            assert result[0]["lon"] == 55.0

    def test_returns_empty_when_no_corridors(self):
        from app.modules.destination_detector import _get_corridor_centers

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []

        result = _get_corridor_centers(db)
        assert result == []

    def test_skips_corridors_with_invalid_geometry(self):
        from app.modules.destination_detector import _get_corridor_centers

        db = MagicMock()
        corridor = MagicMock()
        corridor.corridor_id = 1
        corridor.name = "Bad"
        corridor.geometry = "invalid"

        db.query.return_value.filter.return_value.all.return_value = [corridor]

        with patch("app.utils.geo.load_geometry", return_value=None):
            result = _get_corridor_centers(db)
            assert result == []
