"""Tests for vessel metadata enrichment via GFW API."""

from unittest.mock import MagicMock, patch

from app.models.base import FlagRiskEnum
from app.modules.vessel_enrichment import enrich_vessels_from_gfw, infer_pi_coverage


def _make_mock_db(vessels):
    """Build a mock DB that returns vessels from the query chain."""
    db = MagicMock()
    # The actual code: db.query(Vessel).filter(...).limit(limit).all()
    # MagicMock auto-chains, so we just set the terminal .all()
    db.query.return_value.filter.return_value.limit.return_value.all.return_value = vessels
    return db


def _make_vessel(
    mmsi,
    imo=None,
    deadweight=None,
    year_built=None,
    flag=None,
    flag_risk=None,
    vessel_type=None,
    is_heuristic_dwt=False,
):
    """Create a vessel-like object with real attributes (not MagicMock attributes)."""

    class FakeVessel:
        pass

    v = FakeVessel()
    v.mmsi = mmsi
    v.imo = imo
    v.deadweight = deadweight
    v.year_built = year_built
    v.flag = flag
    v.flag_risk_category = flag_risk
    v.vessel_type = vessel_type
    v.is_heuristic_dwt = is_heuristic_dwt
    v.vessel_id = hash(mmsi) % 10000  # Stable fake ID for enriched_ids tracking
    return v


@patch("app.modules.vessel_enrichment.time.sleep")
@patch("app.modules.gfw_client.search_vessel")
def test_enrich_populates_missing_fields(mock_search, mock_sleep):
    """Vessel with no DWT gets enriched from GFW (non-tanker: DWT = GT)."""
    vessel = _make_vessel("273123456", flag="RU", flag_risk=FlagRiskEnum.HIGH_RISK)
    db = _make_mock_db([vessel])

    mock_search.return_value = [
        {
            "mmsi": "273123456",
            "imo": "9876543",
            "tonnage_gt": 120000,
            "flag": "RU",
            "year_built": 2001,
        }
    ]

    result = enrich_vessels_from_gfw(db, token="test-token", limit=10)

    assert result["enriched"] == 1
    assert result["failed"] == 0
    assert vessel.imo == "9876543"
    # Non-tanker (no vessel_type): DWT = GT directly
    assert vessel.deadweight == 120000.0
    assert vessel.year_built == 2001
    db.commit.assert_called_once()


@patch("app.modules.vessel_enrichment.time.sleep")
@patch("app.modules.gfw_client.search_vessel")
def test_enrich_tanker_gt_to_dwt_conversion(mock_search, mock_sleep):
    """Tanker vessel: DWT = GT × 1.5 conversion factor."""
    vessel = _make_vessel(
        "273123456", flag="RU", flag_risk=FlagRiskEnum.HIGH_RISK, vessel_type="Crude Oil Tanker"
    )
    db = _make_mock_db([vessel])

    mock_search.return_value = [
        {
            "mmsi": "273123456",
            "imo": "9876543",
            "tonnage_gt": 80000,
            "flag": "RU",
            "year_built": 2001,
        }
    ]

    result = enrich_vessels_from_gfw(db, token="test-token", limit=10)

    assert result["enriched"] == 1
    # Tanker: DWT = GT × 1.5
    assert vessel.deadweight == 120000.0  # 80000 × 1.5
    db.commit.assert_called_once()


@patch("app.modules.vessel_enrichment.time.sleep")
@patch("app.modules.gfw_client.search_vessel")
def test_enrich_skips_already_populated(mock_search, mock_sleep):
    """Vessel with existing metadata fields is not overwritten."""
    vessel = _make_vessel(
        "366000001",
        imo="1234567",
        deadweight=150000.0,
        year_built=2005,
        flag="US",
        flag_risk=FlagRiskEnum.LOW_RISK,
    )
    db = _make_mock_db([vessel])

    mock_search.return_value = [
        {
            "mmsi": "366000001",
            "imo": "9999999",
            "tonnage_gt": 200000,
            "flag": "PA",
        }
    ]

    result = enrich_vessels_from_gfw(db, token="test-token", limit=10)

    # Vessel already had imo, deadweight, flag — nothing should change
    assert result["skipped"] == 1
    assert result["enriched"] == 0
    assert vessel.imo == "1234567"  # unchanged
    assert vessel.deadweight == 150000.0  # unchanged


@patch("app.modules.vessel_enrichment.time.sleep")
@patch("app.modules.gfw_client.search_vessel")
def test_enrich_handles_api_failure(mock_search, mock_sleep):
    """GFW API error is counted as failed, not crashing."""
    vessel = _make_vessel("273123456")
    db = _make_mock_db([vessel])
    mock_search.side_effect = Exception("GFW API timeout")

    result = enrich_vessels_from_gfw(db, token="test-token", limit=10)

    assert result["failed"] == 1
    assert result["enriched"] == 0


@patch("app.modules.vessel_enrichment.time.sleep")
@patch("app.modules.gfw_client.search_vessel")
def test_enrich_no_results_skips(mock_search, mock_sleep):
    """When GFW returns no results, vessel is skipped."""
    vessel = _make_vessel("273123456")
    db = _make_mock_db([vessel])
    mock_search.return_value = []

    result = enrich_vessels_from_gfw(db, token="test-token", limit=10)

    assert result["skipped"] == 1
    assert result["enriched"] == 0


def test_infer_pi_coverage_noop():
    """infer_pi_coverage is disabled to prevent circular double-counting with sanctions."""
    db = MagicMock()
    result = infer_pi_coverage(db)

    assert result == {"lapsed": 0, "unchanged": 0}
    # Must not touch the database at all
    db.query.assert_not_called()
    db.commit.assert_not_called()


@patch("app.modules.vessel_enrichment.time.sleep")
@patch("app.modules.gfw_client.search_vessel")
def test_enrich_stores_vessel_type_before_dwt(mock_search, mock_sleep):
    """Regression: vessel_type from GFW is stored BEFORE DWT computation.

    The bug: _is_likely_tanker() was called with no vessel_type set, giving
    the wrong multiplier (1.0 instead of 1.5 for tankers).
    Fix: store vessel_type first, then compute DWT.
    """
    vessel = _make_vessel("273900001", flag="RU", flag_risk=FlagRiskEnum.HIGH_RISK)
    assert vessel.vessel_type is None
    assert vessel.deadweight is None

    db = _make_mock_db([vessel])
    mock_search.return_value = [
        {
            "mmsi": "273900001",
            "imo": "9111111",
            "vessel_type": "Crude Oil Tanker",
            "tonnage_gt": 50000,
            "flag": "RU",
            "year_built": 2008,
        }
    ]

    result = enrich_vessels_from_gfw(db, token="test-token", limit=10)

    assert result["enriched"] == 1
    assert vessel.vessel_type == "Crude Oil Tanker"
    # DWT = 50000 × 1.5 (tanker multiplier applied because vessel_type was set first)
    assert vessel.deadweight == 75000.0  # NOT 50000.0 (bug outcome)
    assert vessel.is_heuristic_dwt is True
    db.commit.assert_called_once()


@patch("app.modules.vessel_enrichment.time.sleep")
@patch("app.modules.gfw_client.search_vessel")
def test_enrich_sets_is_heuristic_dwt_for_gt_derived(mock_search, mock_sleep):
    """is_heuristic_dwt=True when DWT is derived from GFW GT heuristic."""
    vessel = _make_vessel("273900002")
    assert vessel.is_heuristic_dwt is False

    db = _make_mock_db([vessel])
    mock_search.return_value = [
        {
            "mmsi": "273900002",
            "imo": "9222222",
            "tonnage_gt": 30000,
        }
    ]

    result = enrich_vessels_from_gfw(db, token="test-token", limit=10)

    assert result["enriched"] == 1
    assert vessel.deadweight == 30000.0  # Non-tanker: GT × 1.0
    assert vessel.is_heuristic_dwt is True
    db.commit.assert_called_once()


@patch("app.modules.vessel_enrichment.time.sleep")
@patch("app.modules.gfw_client.search_vessel")
def test_enrich_re_enriches_heuristic_dwt_vessels(mock_search, mock_sleep):
    """Vessels with is_heuristic_dwt=True are re-enriched with updated GT value."""
    vessel = _make_vessel("273900003", deadweight=50000.0, is_heuristic_dwt=True)

    db = _make_mock_db([vessel])
    mock_search.return_value = [
        {
            "mmsi": "273900003",
            "imo": "9333333",
            "tonnage_gt": 60000,
        }
    ]

    result = enrich_vessels_from_gfw(db, token="test-token", limit=10)

    assert result["enriched"] == 1
    assert vessel.deadweight == 60000.0  # Updated from 50000 to 60000
    assert vessel.is_heuristic_dwt is True
    db.commit.assert_called_once()


@patch("app.modules.vessel_enrichment.time.sleep")
@patch("app.modules.gfw_client.search_vessel")
def test_enrich_non_tanker_uses_1x_gt_multiplier(mock_search, mock_sleep):
    """Non-tanker vessel_type uses GT directly (1.0×), not the 1.5× tanker factor."""
    vessel = _make_vessel("273900004", flag="PA")

    db = _make_mock_db([vessel])
    mock_search.return_value = [
        {
            "mmsi": "273900004",
            "vessel_type": "General Cargo",
            "tonnage_gt": 10000,
        }
    ]

    result = enrich_vessels_from_gfw(db, token="test-token", limit=10)

    assert result["enriched"] == 1
    assert vessel.vessel_type == "General Cargo"
    assert vessel.deadweight == 10000.0  # NOT 15000.0 (which would be 1.5× tanker)
    assert vessel.is_heuristic_dwt is True
    db.commit.assert_called_once()
