"""Tests for GFW vessel detection importer (FR8)."""
import pytest


def test_parse_gfw_row_valid():
    from app.modules.gfw_import import parse_gfw_row
    row = {
        "detect_id": "GFW_001",
        "timestamp": "2026-01-15T14:30:00Z",
        "lat": "37.5",
        "lon": "23.1",
        "vessel_length_m": "180",
        "vessel_score": "0.92",
        "vessel_type": "cargo",
    }
    result = parse_gfw_row(row)
    assert result["detection_lat"] == 37.5
    assert result["detection_lon"] == 23.1
    assert result["length_estimate_m"] == 180.0
    assert result["model_confidence"] == 0.92


def test_parse_gfw_row_rejects_invalid_lat():
    from app.modules.gfw_import import parse_gfw_row
    row = {
        "detect_id": "X", "timestamp": "2026-01-15T00:00:00Z",
        "lat": "999", "lon": "23.1",
        "vessel_length_m": "50", "vessel_score": "0.5", "vessel_type": "cargo",
    }
    with pytest.raises(ValueError, match="lat"):
        parse_gfw_row(row)


def test_parse_gfw_row_rejects_bad_timestamp():
    from app.modules.gfw_import import parse_gfw_row
    row = {
        "detect_id": "X", "timestamp": "not-a-date",
        "lat": "37.0", "lon": "23.0",
        "vessel_length_m": "50", "vessel_score": "0.5", "vessel_type": "cargo",
    }
    with pytest.raises(ValueError, match="timestamp"):
        parse_gfw_row(row)


def test_parse_gfw_row_rejects_invalid_lon():
    from app.modules.gfw_import import parse_gfw_row
    row = {
        "detect_id": "X", "timestamp": "2026-01-15T00:00:00Z",
        "lat": "37.0", "lon": "200.0",
        "vessel_length_m": "50", "vessel_score": "0.5", "vessel_type": "cargo",
    }
    with pytest.raises(ValueError, match="lon"):
        parse_gfw_row(row)
