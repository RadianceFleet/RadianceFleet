"""Tests for the data fetcher module.

All HTTP calls are mocked — no real network traffic.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data_dir(tmp_path):
    """Provide a temporary directory acting as the data download folder."""
    return tmp_path / "data"


@pytest.fixture
def _patch_settings(tmp_data_dir):
    """Patch settings.DATA_DIR and DATA_FETCH_TIMEOUT for all tests."""
    with patch("app.modules.data_fetcher.settings") as mock_settings:
        mock_settings.DATA_DIR = str(tmp_data_dir)
        mock_settings.DATA_FETCH_TIMEOUT = 5.0
        yield mock_settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_OFAC_CSV = (
    "ent_num,SDN_NAME,SDN_TYPE,VESSEL_ID,REMARKS\n"
    '1234,"TEST VESSEL","Vessel","123456789","sanctioned"\n'
)

VALID_OPENSANCTIONS_JSON = json.dumps([
    {
        "schema": "Vessel",
        "caption": "TEST VESSEL",
        "datasets": ["ofac_sdn"],
        "properties": {"name": ["TEST VESSEL"], "mmsi": ["123456789"]},
    }
])


def _mock_stream_response(content: bytes, status_code: int = 200, headers: dict = None):
    """Create a mock httpx streaming response."""
    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.iter_bytes = MagicMock(return_value=iter([content]))
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"HTTP {status_code}",
            request=MagicMock(),
            response=response,
        )
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    return response


def _mock_client(stream_response):
    """Create a mock httpx.Client with a stream method."""
    client = MagicMock()
    client.stream = MagicMock(return_value=stream_response)
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# Tests: fetch_ofac_sdn
# ---------------------------------------------------------------------------

class TestFetchOfacSdn:
    def test_successful_download(self, tmp_data_dir, _patch_settings):
        """Download OFAC SDN CSV and validate it."""
        from app.modules.data_fetcher import fetch_ofac_sdn

        response = _mock_stream_response(VALID_OFAC_CSV.encode())
        client = _mock_client(response)

        with patch("app.modules.data_fetcher.httpx.Client", return_value=client):
            result = fetch_ofac_sdn(tmp_data_dir)

        assert result["status"] == "downloaded"
        assert result["path"] is not None
        assert result["path"].exists()
        assert result["error"] is None
        # File should contain the CSV content
        assert "SDN_TYPE" in result["path"].read_text()

    def test_network_error(self, tmp_data_dir, _patch_settings):
        """Connection error returns friendly message."""
        import httpx
        from app.modules.data_fetcher import fetch_ofac_sdn

        client = MagicMock()
        response_ctx = MagicMock()
        response_ctx.__enter__ = MagicMock(side_effect=httpx.ConnectError("refused"))
        response_ctx.__exit__ = MagicMock(return_value=False)
        client.stream = MagicMock(return_value=response_ctx)
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        with patch("app.modules.data_fetcher.httpx.Client", return_value=client):
            result = fetch_ofac_sdn(tmp_data_dir)

        assert result["status"] == "error"
        assert "ConnectionError" in result["error"]
        assert "Download manually" in result["error"]

    def test_http_error(self, tmp_data_dir, _patch_settings):
        """HTTP 500 returns error status."""
        from app.modules.data_fetcher import fetch_ofac_sdn

        response = _mock_stream_response(b"", status_code=500)
        client = _mock_client(response)

        with patch("app.modules.data_fetcher.httpx.Client", return_value=client):
            result = fetch_ofac_sdn(tmp_data_dir)

        assert result["status"] == "error"
        assert "500" in result["error"]

    def test_validation_rejects_corrupted(self, tmp_data_dir, _patch_settings):
        """Corrupted CSV (no expected headers) is rejected."""
        from app.modules.data_fetcher import fetch_ofac_sdn

        bad_csv = b"col1,col2,col3\nfoo,bar,baz\n"
        response = _mock_stream_response(bad_csv)
        client = _mock_client(response)

        with patch("app.modules.data_fetcher.httpx.Client", return_value=client):
            result = fetch_ofac_sdn(tmp_data_dir)

        assert result["status"] == "error"
        assert "validation" in result["error"].lower()

    def test_etag_304_not_modified(self, tmp_data_dir, _patch_settings):
        """ETag-based conditional GET returns up_to_date when 304."""
        from app.modules.data_fetcher import fetch_ofac_sdn, _save_metadata

        # Pre-populate metadata with an etag
        tmp_data_dir.mkdir(parents=True, exist_ok=True)
        _save_metadata(tmp_data_dir, {
            "ofac": {
                "etag": '"abc123"',
                "last_modified": None,
                "downloaded_at": "2026-02-25",
                "url": "https://example.com",
            }
        })
        # Also create a "previous" download file so _find_latest works
        prev_file = tmp_data_dir / "ofac_sdn_2026-02-25.csv"
        prev_file.write_text(VALID_OFAC_CSV)

        response = _mock_stream_response(b"", status_code=304)
        client = _mock_client(response)

        with patch("app.modules.data_fetcher.httpx.Client", return_value=client):
            result = fetch_ofac_sdn(tmp_data_dir)

        assert result["status"] == "up_to_date"
        assert result["error"] is None

    def test_force_ignores_etag(self, tmp_data_dir, _patch_settings):
        """--force re-downloads even when etag is cached."""
        from app.modules.data_fetcher import fetch_ofac_sdn, _save_metadata

        tmp_data_dir.mkdir(parents=True, exist_ok=True)
        _save_metadata(tmp_data_dir, {
            "ofac": {"etag": '"abc123"', "downloaded_at": "2026-02-25"}
        })

        response = _mock_stream_response(VALID_OFAC_CSV.encode())
        client = _mock_client(response)

        with patch("app.modules.data_fetcher.httpx.Client", return_value=client):
            result = fetch_ofac_sdn(tmp_data_dir, force=True)

        assert result["status"] == "downloaded"
        # Verify no If-None-Match header was sent
        call_args = client.stream.call_args
        headers_sent = call_args.kwargs.get("headers", call_args[1].get("headers", {}))
        assert "If-None-Match" not in headers_sent


# ---------------------------------------------------------------------------
# Tests: fetch_opensanctions_vessels
# ---------------------------------------------------------------------------

class TestFetchOpenSanctions:
    def test_successful_download(self, tmp_data_dir, _patch_settings):
        """Download OpenSanctions JSON and validate it."""
        from app.modules.data_fetcher import fetch_opensanctions_vessels

        response = _mock_stream_response(VALID_OPENSANCTIONS_JSON.encode())
        client = _mock_client(response)

        with patch("app.modules.data_fetcher.httpx.Client", return_value=client):
            result = fetch_opensanctions_vessels(tmp_data_dir)

        assert result["status"] == "downloaded"
        assert result["path"].exists()
        content = json.loads(result["path"].read_text())
        assert isinstance(content, list)
        assert content[0]["schema"] == "Vessel"

    def test_validation_rejects_non_json(self, tmp_data_dir, _patch_settings):
        """Non-JSON content is rejected."""
        from app.modules.data_fetcher import fetch_opensanctions_vessels

        response = _mock_stream_response(b"this is not json")
        client = _mock_client(response)

        with patch("app.modules.data_fetcher.httpx.Client", return_value=client):
            result = fetch_opensanctions_vessels(tmp_data_dir)

        assert result["status"] == "error"
        assert "validation" in result["error"].lower()

    def test_validation_rejects_wrong_structure(self, tmp_data_dir, _patch_settings):
        """JSON that isn't a vessel entity array is rejected."""
        from app.modules.data_fetcher import fetch_opensanctions_vessels

        bad_json = json.dumps({"key": "value"}).encode()
        response = _mock_stream_response(bad_json)
        client = _mock_client(response)

        with patch("app.modules.data_fetcher.httpx.Client", return_value=client):
            result = fetch_opensanctions_vessels(tmp_data_dir)

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Tests: fetch_all
# ---------------------------------------------------------------------------

class TestFetchAll:
    def test_partial_failure(self, tmp_data_dir, _patch_settings):
        """One source fails, other succeeds — errors list has one entry."""
        from app.modules.data_fetcher import fetch_all

        def mock_ofac(*a, **kw):
            return {"path": None, "status": "error", "error": "Network error"}

        def mock_opensanctions(*a, **kw):
            return {"path": tmp_data_dir / "test.json", "status": "downloaded", "error": None}

        with patch("app.modules.data_fetcher.fetch_ofac_sdn", side_effect=mock_ofac), \
             patch("app.modules.data_fetcher.fetch_opensanctions_vessels", side_effect=mock_opensanctions):
            result = fetch_all(tmp_data_dir)

        assert len(result["errors"]) == 1
        assert "OFAC" in result["errors"][0]
        assert result["opensanctions"]["status"] == "downloaded"

    def test_all_succeed(self, tmp_data_dir, _patch_settings):
        """Both sources succeed — no errors."""
        from app.modules.data_fetcher import fetch_all

        def mock_fetch(*a, **kw):
            return {"path": tmp_data_dir / "test", "status": "downloaded", "error": None}

        with patch("app.modules.data_fetcher.fetch_ofac_sdn", side_effect=mock_fetch), \
             patch("app.modules.data_fetcher.fetch_opensanctions_vessels", side_effect=mock_fetch):
            result = fetch_all(tmp_data_dir)

        assert result["errors"] == []


# ---------------------------------------------------------------------------
# Tests: validation helpers
# ---------------------------------------------------------------------------

class TestValidation:
    def test_validate_ofac_csv_valid(self, tmp_path):
        from app.modules.data_fetcher import _validate_ofac_csv

        csv_path = tmp_path / "sdn.csv"
        csv_path.write_text(VALID_OFAC_CSV)
        assert _validate_ofac_csv(csv_path) is True

    def test_validate_ofac_csv_invalid(self, tmp_path):
        from app.modules.data_fetcher import _validate_ofac_csv

        csv_path = tmp_path / "bad.csv"
        csv_path.write_text("col1,col2\nfoo,bar\n")
        assert _validate_ofac_csv(csv_path) is False

    def test_validate_opensanctions_valid(self, tmp_path):
        from app.modules.data_fetcher import _validate_opensanctions_json

        json_path = tmp_path / "vessels.json"
        json_path.write_text(VALID_OPENSANCTIONS_JSON)
        assert _validate_opensanctions_json(json_path) is True

    def test_validate_opensanctions_invalid_not_list(self, tmp_path):
        from app.modules.data_fetcher import _validate_opensanctions_json

        json_path = tmp_path / "bad.json"
        json_path.write_text('{"not": "a list"}')
        assert _validate_opensanctions_json(json_path) is False

    def test_validate_opensanctions_invalid_no_schema(self, tmp_path):
        from app.modules.data_fetcher import _validate_opensanctions_json

        json_path = tmp_path / "bad.json"
        json_path.write_text('[{"name": "test"}]')
        assert _validate_opensanctions_json(json_path) is False


# ---------------------------------------------------------------------------
# Tests: metadata persistence
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_save_and_load(self, tmp_path):
        from app.modules.data_fetcher import _save_metadata, _load_metadata

        meta = {"ofac": {"etag": '"abc"', "downloaded_at": "2026-02-26"}}
        _save_metadata(tmp_path, meta)
        loaded = _load_metadata(tmp_path)
        assert loaded["ofac"]["etag"] == '"abc"'

    def test_load_missing_returns_empty(self, tmp_path):
        from app.modules.data_fetcher import _load_metadata

        assert _load_metadata(tmp_path / "nonexistent") == {}

    def test_load_corrupted_returns_empty(self, tmp_path):
        from app.modules.data_fetcher import _load_metadata

        (tmp_path / ".fetch_metadata.json").write_text("not json{{{")
        assert _load_metadata(tmp_path) == {}


# ---------------------------------------------------------------------------
# Tests: timeout handling
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_timeout_error(self, tmp_data_dir, _patch_settings):
        """Timeout returns friendly error with retry hint."""
        import httpx
        from app.modules.data_fetcher import fetch_ofac_sdn

        client = MagicMock()
        response_ctx = MagicMock()
        response_ctx.__enter__ = MagicMock(side_effect=httpx.TimeoutException("timed out"))
        response_ctx.__exit__ = MagicMock(return_value=False)
        client.stream = MagicMock(return_value=response_ctx)
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        with patch("app.modules.data_fetcher.httpx.Client", return_value=client):
            result = fetch_ofac_sdn(tmp_data_dir)

        assert result["status"] == "error"
        assert "Timed out" in result["error"]
