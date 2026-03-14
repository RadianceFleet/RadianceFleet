"""Tests for Equasis ownership chain extraction."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.modules.equasis_client import _parse_company_history


# ── HTML parsing tests ──────────────────────────────────────────────────────

SAMPLE_COMPANY_HTML = """
<html><body>
<table>
  <tr>
    <td>C12345</td>
    <td>Registered Owner</td>
    <td>Oceanic Shipping Corp</td>
    <td>Panama</td>
    <td>2020-01-15</td>
  </tr>
  <tr>
    <td>C67890</td>
    <td>ISM Manager</td>
    <td>Global Ship Management Ltd</td>
    <td>Greece</td>
    <td>2019-06-01</td>
  </tr>
  <tr>
    <td>C11111</td>
    <td>Ship Manager</td>
    <td>Baltic Marine Services</td>
    <td>Cyprus</td>
    <td>2021-03-10</td>
  </tr>
  <tr>
    <td>C22222</td>
    <td>DOC Company</td>
    <td>Safety Compliance Ltd</td>
    <td>Malta</td>
    <td>2018-11-20</td>
  </tr>
</table>
</body></html>
"""


def test_parse_company_history_extracts_all_roles():
    result = _parse_company_history(SAMPLE_COMPANY_HTML)
    assert len(result) == 4
    roles = [r["role"] for r in result]
    assert "Registered Owner" in roles
    assert "ISM Manager" in roles
    assert "Ship Manager" in roles
    assert "DOC Company" in roles


def test_parse_company_history_extracts_fields():
    result = _parse_company_history(SAMPLE_COMPANY_HTML)
    owner = next(r for r in result if r["role"] == "Registered Owner")
    assert owner["company_name"] == "Oceanic Shipping Corp"
    assert owner["company_id"] == "C12345"
    assert owner["flag"] == "Panama"
    assert owner["since"] == "2020-01-15"


def test_parse_company_history_empty_html():
    result = _parse_company_history("<html><body></body></html>")
    assert result == []


def test_parse_company_history_incomplete_rows():
    """Rows with fewer than 5 columns are skipped."""
    html = "<html><body><table><tr><td>A</td><td>B</td></tr></table></body></html>"
    result = _parse_company_history(html)
    assert result == []


def test_parse_company_history_skips_empty_names():
    html = """
    <table><tr>
      <td>C99</td><td>Owner</td><td></td><td>UK</td><td>2020-01-01</td>
    </tr></table>
    """
    result = _parse_company_history(html)
    assert result == []


# ── Orchestrator tests ──────────────────────────────────────────────────────


@pytest.fixture()
def mock_db():
    return MagicMock()


@pytest.fixture()
def mock_vessel():
    v = MagicMock()
    v.vessel_id = 1
    v.imo = "9123456"
    v.name = "Test Vessel"
    return v


def test_extract_ownership_disabled(mock_db):
    """When EQUASIS_SCRAPING_ENABLED=false, returns empty dict."""
    with patch(
        "app.modules.equasis_ownership._create_client",
        side_effect=RuntimeError("disabled"),
    ):
        from app.modules.equasis_ownership import extract_ownership_chain

        result = extract_ownership_chain(mock_db, 1)
    assert result == {}


def test_extract_ownership_vessel_not_found(mock_db):
    """Returns empty dict if vessel doesn't exist."""
    mock_db.query.return_value.filter.return_value.first.return_value = None
    with patch("app.modules.equasis_ownership._create_client"):
        from app.modules.equasis_ownership import extract_ownership_chain

        result = extract_ownership_chain(mock_db, 999)
    assert result == {}


def test_extract_ownership_vessel_no_imo(mock_db, mock_vessel):
    """Returns empty dict if vessel has no IMO."""
    mock_vessel.imo = None
    mock_db.query.return_value.filter.return_value.first.return_value = mock_vessel
    with patch("app.modules.equasis_ownership._create_client"):
        from app.modules.equasis_ownership import extract_ownership_chain

        result = extract_ownership_chain(mock_db, 1)
    assert result == {}


def test_extract_ownership_success(mock_db, mock_vessel):
    """Successful extraction returns chain data."""
    mock_db.query.return_value.filter.return_value.first.return_value = mock_vessel
    chain_data = [
        {
            "role": "Registered Owner",
            "company_name": "Oceanic Corp",
            "company_id": "C1",
            "flag": "PA",
            "since": "2020-01-01",
        },
    ]
    mock_client = MagicMock()
    mock_client.get_ownership_chain.return_value = chain_data

    with (
        patch("app.modules.equasis_ownership._create_client", return_value=mock_client),
        patch(
            "app.modules.equasis_ownership.import_equasis_ownership",
            return_value=[MagicMock(owner_name="Oceanic Corp", ownership_type="registered_owner", country="PA")],
        ) as mock_import,
    ):
        from app.modules.equasis_ownership import extract_ownership_chain

        result = extract_ownership_chain(mock_db, 1)

    assert result["vessel_id"] == 1
    assert result["imo"] == "9123456"
    assert result["count"] == 1
    assert result["records"][0]["owner_name"] == "Oceanic Corp"
    mock_import.assert_called_once_with(mock_db, 1, chain_data)


def test_extract_ownership_max_hops(mock_db, mock_vessel):
    """Chain data is truncated to EQUASIS_OWNERSHIP_MAX_HOPS."""
    mock_db.query.return_value.filter.return_value.first.return_value = mock_vessel
    # Create chain with 10 entries
    chain_data = [
        {
            "role": f"Role{i}",
            "company_name": f"Company{i}",
            "company_id": f"C{i}",
            "flag": "XX",
            "since": "2020-01-01",
        }
        for i in range(10)
    ]
    mock_client = MagicMock()
    mock_client.get_ownership_chain.return_value = chain_data

    with (
        patch("app.modules.equasis_ownership._create_client", return_value=mock_client),
        patch("app.modules.equasis_ownership.settings") as mock_settings,
        patch(
            "app.modules.equasis_ownership.import_equasis_ownership",
            return_value=[],
        ) as mock_import,
    ):
        mock_settings.EQUASIS_OWNERSHIP_MAX_HOPS = 3
        from app.modules.equasis_ownership import extract_ownership_chain

        extract_ownership_chain(mock_db, 1)

    # import_equasis_ownership should receive truncated list
    call_args = mock_import.call_args[0]
    assert len(call_args[2]) == 3


def test_extract_ownership_empty_chain(mock_db, mock_vessel):
    """When Equasis returns empty chain, returns result with count=0."""
    mock_db.query.return_value.filter.return_value.first.return_value = mock_vessel
    mock_client = MagicMock()
    mock_client.get_ownership_chain.return_value = []

    with patch("app.modules.equasis_ownership._create_client", return_value=mock_client):
        from app.modules.equasis_ownership import extract_ownership_chain

        result = extract_ownership_chain(mock_db, 1)

    assert result["count"] == 0
    assert result["records"] == []


def test_batch_extract_disabled(mock_db):
    """Batch returns empty results when scraping disabled."""
    with patch(
        "app.modules.equasis_ownership._create_client",
        side_effect=RuntimeError("disabled"),
    ):
        from app.modules.equasis_ownership import batch_extract_ownership

        result = batch_extract_ownership(mock_db)
    assert result["processed"] == 0
    assert result["results"] == []
    assert result["errors"] == 0


def test_batch_extract_processes_vessels(mock_db, mock_vessel):
    """Batch processes multiple vessels with rate limiting."""
    mock_vessel2 = MagicMock()
    mock_vessel2.vessel_id = 2
    mock_vessel2.imo = "9654321"

    mock_db.query.return_value.filter.return_value.filter.return_value.limit.return_value.all.return_value = [
        mock_vessel,
        mock_vessel2,
    ]

    with (
        patch("app.modules.equasis_ownership._create_client"),
        patch(
            "app.modules.equasis_ownership.extract_ownership_chain",
            return_value={"vessel_id": 1, "count": 1, "records": [], "imo": "9123456"},
        ),
        patch("app.modules.equasis_ownership.time") as mock_time,
        patch("app.modules.equasis_ownership.settings") as mock_settings,
    ):
        mock_settings.EQUASIS_OWNERSHIP_RATE_LIMIT_S = 0.0
        from app.modules.equasis_ownership import batch_extract_ownership

        result = batch_extract_ownership(mock_db, vessel_ids=[1, 2], limit=10)

    assert result["processed"] == 2
    assert len(result["results"]) == 2
    # Rate limiting sleep called between vessels (once for 2 vessels)
    mock_time.sleep.assert_called()


def test_batch_extract_handles_errors(mock_db, mock_vessel):
    """Batch continues processing even when individual vessels fail."""
    mock_db.query.return_value.filter.return_value.filter.return_value.limit.return_value.all.return_value = [
        mock_vessel,
    ]

    with (
        patch("app.modules.equasis_ownership._create_client"),
        patch(
            "app.modules.equasis_ownership.extract_ownership_chain",
            side_effect=Exception("network error"),
        ),
        patch("app.modules.equasis_ownership.settings") as mock_settings,
    ):
        mock_settings.EQUASIS_OWNERSHIP_RATE_LIMIT_S = 0.0
        from app.modules.equasis_ownership import batch_extract_ownership

        result = batch_extract_ownership(mock_db, vessel_ids=[1])

    assert result["processed"] == 1
    assert result["errors"] == 1


# ── import_equasis_ownership tests ──────────────────────────────────────────


def test_import_equasis_ownership_creates_records(mock_db):
    """import_equasis_ownership creates VesselOwner records."""
    # Mock the query to return no existing records
    mock_db.query.return_value.filter.return_value.first.return_value = None

    chain_data = [
        {
            "role": "Registered Owner",
            "company_name": "Test Owner",
            "company_id": "C1",
            "flag": "PA",
            "since": "2020-01-01",
        },
        {
            "role": "ISM Manager",
            "company_name": "Test ISM",
            "company_id": "C2",
            "flag": "GR",
            "since": "2019-01-01",
        },
    ]

    from app.modules.ownership_graph import import_equasis_ownership

    records = import_equasis_ownership(mock_db, vessel_id=1, chain_data=chain_data)

    assert len(records) == 2
    assert mock_db.add.call_count == 2
    mock_db.commit.assert_called_once()


def test_import_equasis_ownership_skips_empty_names(mock_db):
    """Entries with empty company_name are skipped."""
    chain_data = [
        {"role": "Owner", "company_name": "", "company_id": "C1", "flag": "PA", "since": ""},
    ]

    from app.modules.ownership_graph import import_equasis_ownership

    records = import_equasis_ownership(mock_db, vessel_id=1, chain_data=chain_data)

    assert len(records) == 0
    mock_db.add.assert_not_called()
