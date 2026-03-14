"""Tests for gas flaring platform filter."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml


class TestLoadFlaringPlatforms:
    """Tests for load_flaring_platforms."""

    def test_loads_platforms_from_yaml(self, tmp_path):
        """Loads platforms from YAML config."""
        config = {
            "platforms": [
                {"name": "Test Platform", "lat": 55.5, "lon": 18.1, "region": "Baltic"},
                {"name": "Test Platform 2", "lat": 56.0, "lon": 19.0, "region": "North Sea"},
            ]
        }
        config_path = tmp_path / "gas_flaring.yaml"
        config_path.write_text(yaml.dump(config))

        from app.modules.gas_flaring_filter import load_flaring_platforms

        platforms = load_flaring_platforms(str(config_path))
        assert len(platforms) == 2
        assert platforms[0]["name"] == "Test Platform"

    def test_returns_empty_for_missing_file(self, tmp_path):
        """Returns empty list when config file doesn't exist."""
        from app.modules.gas_flaring_filter import load_flaring_platforms

        platforms = load_flaring_platforms(str(tmp_path / "nonexistent.yaml"))
        assert platforms == []

    def test_skips_entries_without_lat_lon(self, tmp_path):
        """Skips platform entries missing lat or lon."""
        config = {
            "platforms": [
                {"name": "Valid", "lat": 55.5, "lon": 18.1},
                {"name": "Missing lon", "lat": 55.5},
                {"name": "Missing lat", "lon": 18.1},
            ]
        }
        config_path = tmp_path / "gas_flaring.yaml"
        config_path.write_text(yaml.dump(config))

        from app.modules.gas_flaring_filter import load_flaring_platforms

        platforms = load_flaring_platforms(str(config_path))
        assert len(platforms) == 1

    def test_empty_platforms_list(self, tmp_path):
        """Returns empty list when platforms list is empty."""
        config = {"platforms": []}
        config_path = tmp_path / "gas_flaring.yaml"
        config_path.write_text(yaml.dump(config))

        from app.modules.gas_flaring_filter import load_flaring_platforms

        platforms = load_flaring_platforms(str(config_path))
        assert platforms == []


class TestIsNearFlaringPlatform:
    """Tests for is_near_flaring_platform."""

    def test_near_platform_returns_true(self):
        """Returns True when detection is near a platform."""
        platforms = [{"name": "Test", "lat": 55.5, "lon": 18.1}]

        from app.modules.gas_flaring_filter import is_near_flaring_platform

        # Same position — distance is 0
        assert is_near_flaring_platform(55.5, 18.1, platforms, radius_nm=5.0) is True

    def test_far_from_platform_returns_false(self):
        """Returns False when detection is far from all platforms."""
        platforms = [{"name": "Test", "lat": 55.5, "lon": 18.1}]

        from app.modules.gas_flaring_filter import is_near_flaring_platform

        # ~300nm away
        assert is_near_flaring_platform(60.0, 25.0, platforms, radius_nm=5.0) is False

    def test_empty_platforms_returns_false(self):
        """Returns False with empty platform list."""
        from app.modules.gas_flaring_filter import is_near_flaring_platform

        assert is_near_flaring_platform(55.5, 18.1, [], radius_nm=5.0) is False


class TestFilterFlaring:
    """Tests for filter_flaring."""

    def test_filters_near_platform(self):
        """Detections near platforms are removed."""
        platforms = [{"name": "Test", "lat": 55.5, "lon": 18.1}]
        detections = [
            {"lat": 55.5, "lon": 18.1, "scene_id": "near"},
            {"lat": 60.0, "lon": 25.0, "scene_id": "far"},
        ]

        from app.modules.gas_flaring_filter import filter_flaring

        result = filter_flaring(detections, platforms, radius_nm=5.0)
        assert len(result) == 1
        assert result[0]["scene_id"] == "far"

    def test_no_platforms_returns_all(self):
        """All detections pass when no platforms defined."""
        detections = [
            {"lat": 55.5, "lon": 18.1, "scene_id": "a"},
            {"lat": 56.0, "lon": 19.0, "scene_id": "b"},
        ]

        from app.modules.gas_flaring_filter import filter_flaring

        result = filter_flaring(detections, [], radius_nm=5.0)
        assert len(result) == 2

    def test_all_near_platform_returns_empty(self):
        """All detections filtered when all near platform."""
        platforms = [{"name": "Test", "lat": 55.5, "lon": 18.1}]
        detections = [
            {"lat": 55.5, "lon": 18.1, "scene_id": "a"},
            {"lat": 55.50001, "lon": 18.10001, "scene_id": "b"},
        ]

        from app.modules.gas_flaring_filter import filter_flaring

        result = filter_flaring(detections, platforms, radius_nm=5.0)
        assert len(result) == 0

    def test_custom_radius(self):
        """Custom radius changes filter behavior."""
        platforms = [{"name": "Test", "lat": 55.5, "lon": 18.1}]
        # Detection about 6nm away
        detections = [
            {"lat": 55.6, "lon": 18.1, "scene_id": "mid"},
        ]

        from app.modules.gas_flaring_filter import filter_flaring

        # With small radius, detection passes
        result_small = filter_flaring(detections, platforms, radius_nm=1.0)
        assert len(result_small) == 1

        # With large radius, detection is filtered
        result_large = filter_flaring(detections, platforms, radius_nm=50.0)
        assert len(result_large) == 0


class TestGasFlaringConfig:
    """Tests for the gas_flaring_platforms.yaml config file."""

    def test_config_file_loadable(self):
        """Default config file loads without error."""
        config_path = (
            Path(__file__).resolve().parent.parent.parent
            / "config"
            / "gas_flaring_platforms.yaml"
        )
        if config_path.exists():
            from app.modules.gas_flaring_filter import load_flaring_platforms

            platforms = load_flaring_platforms(str(config_path))
            assert len(platforms) > 0
            for p in platforms:
                assert "lat" in p
                assert "lon" in p
                assert -90 <= p["lat"] <= 90
                assert -180 <= p["lon"] <= 180
