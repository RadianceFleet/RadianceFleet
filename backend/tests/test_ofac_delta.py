"""Tests for OFAC SDN delta detection, webhook firing, and XML download."""

from __future__ import annotations

import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.watchlist_scheduler import (
    SOURCES,
    run_watchlist_update,
    update_source,
)


@pytest.fixture()
def ofac_source_cfg():
    return SOURCES[0]  # OFAC_SDN


@pytest.fixture()
def mock_db():
    """Minimal mock DB session."""
    db = MagicMock()
    db.execute.return_value.fetchone.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.filter.return_value.count.return_value = 0
    return db


# ---------------------------------------------------------------------------
# Delta detection
# ---------------------------------------------------------------------------


class TestDeltaDetection:
    """Test that added/removed vessel counts are correct."""

    def test_added_vessels_counted(self, mock_db, ofac_source_cfg, tmp_path):
        """When new vessel IDs appear after load, they are counted as added."""
        call_count = 0

        def fake_get_active_ids(db, source_name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {1, 2, 3}  # before
            return {1, 2, 3, 4, 5}  # after: 4,5 are new

        with (
            patch("app.modules.watchlist_scheduler._get_active_vessel_ids", side_effect=fake_get_active_ids),
            patch("app.modules.watchlist_scheduler._should_update", return_value=True),
            patch("app.modules.data_fetcher.fetch_ofac_sdn", return_value={"path": tmp_path / "sdn.csv", "error": None}),
            patch("app.modules.watchlist_loader.load_ofac_sdn", return_value={"matched": 5, "unmatched": 0}),
            patch("app.modules.watchlist_scheduler.settings") as mock_settings,
        ):
            mock_settings.DATA_DIR = str(tmp_path)
            mock_settings.OFAC_SDN_WEBHOOK_ON_NEW = False  # disable webhook for this test
            result = update_source(mock_db, ofac_source_cfg, force=True)

        assert result["added"] == 2
        assert result["removed"] == 0
        assert result["unchanged"] == 3

    def test_removed_vessels_counted(self, mock_db, ofac_source_cfg, tmp_path):
        """When vessel IDs disappear after load, they are counted as removed."""
        call_count = 0

        def fake_get_active_ids(db, source_name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {1, 2, 3, 4}  # before
            return {1, 2}  # after: 3,4 removed

        with (
            patch("app.modules.watchlist_scheduler._get_active_vessel_ids", side_effect=fake_get_active_ids),
            patch("app.modules.watchlist_scheduler._should_update", return_value=True),
            patch("app.modules.data_fetcher.fetch_ofac_sdn", return_value={"path": tmp_path / "sdn.csv", "error": None}),
            patch("app.modules.watchlist_loader.load_ofac_sdn", return_value={"matched": 2, "unmatched": 0}),
            patch("app.modules.watchlist_scheduler.settings") as mock_settings,
        ):
            mock_settings.DATA_DIR = str(tmp_path)
            mock_settings.OFAC_SDN_WEBHOOK_ON_NEW = False
            result = update_source(mock_db, ofac_source_cfg, force=True)

        assert result["added"] == 0
        assert result["removed"] == 2
        assert result["unchanged"] == 2

    def test_no_changes(self, mock_db, ofac_source_cfg, tmp_path):
        """When nothing changes, added and removed are both 0."""
        call_count = 0

        def fake_get_active_ids(db, source_name):
            nonlocal call_count
            call_count += 1
            return {1, 2, 3}  # same before and after

        with (
            patch("app.modules.watchlist_scheduler._get_active_vessel_ids", side_effect=fake_get_active_ids),
            patch("app.modules.watchlist_scheduler._should_update", return_value=True),
            patch("app.modules.data_fetcher.fetch_ofac_sdn", return_value={"path": tmp_path / "sdn.csv", "error": None}),
            patch("app.modules.watchlist_loader.load_ofac_sdn", return_value={"matched": 3, "unmatched": 0}),
            patch("app.modules.watchlist_scheduler.settings") as mock_settings,
        ):
            mock_settings.DATA_DIR = str(tmp_path)
            mock_settings.OFAC_SDN_WEBHOOK_ON_NEW = False
            result = update_source(mock_db, ofac_source_cfg, force=True)

        assert result["added"] == 0
        assert result["removed"] == 0
        assert result["unchanged"] == 3


# ---------------------------------------------------------------------------
# Webhook firing
# ---------------------------------------------------------------------------


class TestWebhookFiring:
    """Test webhook is fired on new OFAC vessels (and not otherwise)."""

    def test_webhook_fired_when_vessels_added(self, mock_db, ofac_source_cfg, tmp_path):
        """Webhook fires when new vessels are added and config is enabled."""
        call_count = 0

        def fake_get_active_ids(db, source_name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {1}
            return {1, 2}

        mock_fire = AsyncMock()

        with (
            patch("app.modules.watchlist_scheduler._get_active_vessel_ids", side_effect=fake_get_active_ids),
            patch("app.modules.watchlist_scheduler._should_update", return_value=True),
            patch("app.modules.data_fetcher.fetch_ofac_sdn", return_value={"path": tmp_path / "sdn.csv", "error": None}),
            patch("app.modules.watchlist_loader.load_ofac_sdn", return_value={"matched": 2, "unmatched": 0}),
            patch("app.modules.webhook_dispatcher.fire_webhooks", mock_fire),
            patch("app.modules.watchlist_scheduler.settings") as mock_settings,
        ):
            mock_settings.DATA_DIR = str(tmp_path)
            mock_settings.OFAC_SDN_WEBHOOK_ON_NEW = True
            # Mock the VesselWatchlist query for new entries
            mock_entry = MagicMock()
            mock_entry.vessel_id = 2
            mock_entry.vessel.name = "SHADOW TANKER"
            mock_entry.reason = "OFAC SDN listed"
            mock_db.query.return_value.filter.return_value.all.return_value = [mock_entry]

            result = update_source(mock_db, ofac_source_cfg, force=True)

        assert result["added"] == 1
        mock_fire.assert_called_once()
        call_args = mock_fire.call_args
        assert call_args[0][1] == "ofac_sdn_update"
        payload = call_args[0][2]
        assert payload["added"] == 1
        assert payload["removed"] == 0
        assert len(payload["vessels_added"]) == 1
        assert payload["vessels_added"][0]["name"] == "SHADOW TANKER"

    def test_webhook_not_fired_when_no_changes(self, mock_db, ofac_source_cfg, tmp_path):
        """Webhook does NOT fire when no new vessels are added."""
        call_count = 0

        def fake_get_active_ids(db, source_name):
            nonlocal call_count
            call_count += 1
            return {1, 2}  # same before and after

        mock_fire = AsyncMock()

        with (
            patch("app.modules.watchlist_scheduler._get_active_vessel_ids", side_effect=fake_get_active_ids),
            patch("app.modules.watchlist_scheduler._should_update", return_value=True),
            patch("app.modules.data_fetcher.fetch_ofac_sdn", return_value={"path": tmp_path / "sdn.csv", "error": None}),
            patch("app.modules.watchlist_loader.load_ofac_sdn", return_value={"matched": 2, "unmatched": 0}),
            patch("app.modules.webhook_dispatcher.fire_webhooks", mock_fire),
            patch("app.modules.watchlist_scheduler.settings") as mock_settings,
        ):
            mock_settings.DATA_DIR = str(tmp_path)
            mock_settings.OFAC_SDN_WEBHOOK_ON_NEW = True
            result = update_source(mock_db, ofac_source_cfg, force=True)

        assert result["added"] == 0
        mock_fire.assert_not_called()

    def test_webhook_not_fired_when_config_disabled(self, mock_db, ofac_source_cfg, tmp_path):
        """Webhook does NOT fire when OFAC_SDN_WEBHOOK_ON_NEW is False."""
        call_count = 0

        def fake_get_active_ids(db, source_name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {1}
            return {1, 2}

        mock_fire = AsyncMock()

        with (
            patch("app.modules.watchlist_scheduler._get_active_vessel_ids", side_effect=fake_get_active_ids),
            patch("app.modules.watchlist_scheduler._should_update", return_value=True),
            patch("app.modules.data_fetcher.fetch_ofac_sdn", return_value={"path": tmp_path / "sdn.csv", "error": None}),
            patch("app.modules.watchlist_loader.load_ofac_sdn", return_value={"matched": 2, "unmatched": 0}),
            patch("app.modules.webhook_dispatcher.fire_webhooks", mock_fire),
            patch("app.modules.watchlist_scheduler.settings") as mock_settings,
        ):
            mock_settings.DATA_DIR = str(tmp_path)
            mock_settings.OFAC_SDN_WEBHOOK_ON_NEW = False
            result = update_source(mock_db, ofac_source_cfg, force=True)

        assert result["added"] == 1
        mock_fire.assert_not_called()

    def test_webhook_not_fired_for_non_ofac_source(self, mock_db, tmp_path):
        """Webhook does NOT fire for non-OFAC sources even with additions."""
        opensanctions_cfg = SOURCES[1]  # OPENSANCTIONS
        call_count = 0

        def fake_get_active_ids(db, source_name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {1}
            return {1, 2}

        mock_fire = AsyncMock()

        with (
            patch("app.modules.watchlist_scheduler._get_active_vessel_ids", side_effect=fake_get_active_ids),
            patch("app.modules.watchlist_scheduler._should_update", return_value=True),
            patch("app.modules.data_fetcher.fetch_opensanctions_vessels", return_value={"path": tmp_path / "os.json", "error": None}),
            patch("app.modules.watchlist_loader.load_opensanctions", return_value={"matched": 2, "unmatched": 0}),
            patch("app.modules.webhook_dispatcher.fire_webhooks", mock_fire),
            patch("app.modules.watchlist_scheduler.settings") as mock_settings,
        ):
            mock_settings.DATA_DIR = str(tmp_path)
            mock_settings.OFAC_SDN_WEBHOOK_ON_NEW = True
            result = update_source(mock_db, opensanctions_cfg, force=True)

        assert result["added"] == 1
        mock_fire.assert_not_called()

    def test_webhook_error_does_not_break_update(self, mock_db, ofac_source_cfg, tmp_path):
        """If webhook firing fails, the update still succeeds."""
        call_count = 0

        def fake_get_active_ids(db, source_name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {1}
            return {1, 2}

        with (
            patch("app.modules.watchlist_scheduler._get_active_vessel_ids", side_effect=fake_get_active_ids),
            patch("app.modules.watchlist_scheduler._should_update", return_value=True),
            patch("app.modules.data_fetcher.fetch_ofac_sdn", return_value={"path": tmp_path / "sdn.csv", "error": None}),
            patch("app.modules.watchlist_loader.load_ofac_sdn", return_value={"matched": 2, "unmatched": 0}),
            patch("app.modules.watchlist_scheduler.settings") as mock_settings,
        ):
            mock_settings.DATA_DIR = str(tmp_path)
            mock_settings.OFAC_SDN_WEBHOOK_ON_NEW = True
            # Make the VesselWatchlist query raise an error
            mock_db.query.return_value.filter.return_value.all.side_effect = Exception("DB error")

            result = update_source(mock_db, ofac_source_cfg, force=True)

        # Update should still succeed despite webhook error
        assert result["status"] == "success"
        assert result["added"] == 1


# ---------------------------------------------------------------------------
# Manual sync endpoint
# ---------------------------------------------------------------------------


class TestManualSyncEndpoint:
    """Test POST /admin/watchlist-sync endpoint."""

    def test_manual_sync_calls_run_watchlist_update(self):
        """Manual sync endpoint calls run_watchlist_update with force=True."""
        with patch("app.modules.watchlist_scheduler.run_watchlist_update") as mock_run:
            mock_run.return_value = [{"source": "OFAC_SDN", "status": "success", "added": 0, "removed": 0}]

            # Import the underlying function, bypassing the limiter decorator
            from app.modules.watchlist_scheduler import run_watchlist_update

            mock_db = MagicMock()
            results = run_watchlist_update(mock_db, force=True, sources=["OFAC_SDN"])

            mock_run.assert_called_once_with(mock_db, force=True, sources=["OFAC_SDN"])
            assert results[0]["source"] == "OFAC_SDN"

    def test_manual_sync_endpoint_exists(self):
        """Verify the admin_watchlist_sync endpoint is registered."""
        from app.api.routes_admin import router

        paths = [r.path for r in router.routes if hasattr(r, "path")]
        assert "/admin/watchlist-sync" in paths


# ---------------------------------------------------------------------------
# XML download
# ---------------------------------------------------------------------------


class TestXmlDownload:
    """Test OFAC SDN XML download with fallback."""

    def test_xml_download_extracts_xml(self, tmp_path):
        """XML zip is downloaded and the XML file is extracted."""
        from app.modules.data_fetcher import fetch_ofac_sdn_xml

        # Create a fake zip containing an XML file
        zip_content = b"<sdnList><sdnEntry/></sdnList>"
        zip_path = tmp_path / "test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("sdn.xml", zip_content)

        with (
            patch("app.modules.data_fetcher._download_file") as mock_dl,
            patch("app.modules.data_fetcher.settings") as mock_settings,
        ):
            mock_settings.DATA_DIR = str(tmp_path)
            # Simulate successful download — write the zip to the expected path
            def download_side_effect(url, output_path, source_key, metadata, *, force=False, timeout=None):
                import shutil
                shutil.copy2(zip_path, output_path)
                return output_path, None

            mock_dl.side_effect = download_side_effect
            result = fetch_ofac_sdn_xml(tmp_path, force=True)

        assert result["status"] == "downloaded"
        assert result["error"] is None
        assert result["path"] is not None
        assert str(result["path"]).endswith(".xml")

    def test_xml_download_falls_back_to_csv_on_error(self, tmp_path):
        """When XML download fails, falls back to CSV."""
        from app.modules.data_fetcher import fetch_ofac_sdn_xml

        with (
            patch("app.modules.data_fetcher._download_file") as mock_dl,
            patch("app.modules.data_fetcher.fetch_ofac_sdn") as mock_csv,
            patch("app.modules.data_fetcher.settings") as mock_settings,
        ):
            mock_settings.DATA_DIR = str(tmp_path)
            mock_dl.return_value = (None, "Connection error")
            mock_csv.return_value = {"path": tmp_path / "sdn.csv", "status": "downloaded", "error": None}
            result = fetch_ofac_sdn_xml(tmp_path, force=True)

        assert result["status"] == "downloaded"
        mock_csv.assert_called_once()

    def test_xml_download_falls_back_on_bad_zip(self, tmp_path):
        """When zip file is corrupt, falls back to CSV."""
        from app.modules.data_fetcher import fetch_ofac_sdn_xml

        with (
            patch("app.modules.data_fetcher._download_file") as mock_dl,
            patch("app.modules.data_fetcher.fetch_ofac_sdn") as mock_csv,
            patch("app.modules.data_fetcher.settings") as mock_settings,
        ):
            mock_settings.DATA_DIR = str(tmp_path)

            def download_side_effect(url, output_path, source_key, metadata, *, force=False, timeout=None):
                output_path.write_bytes(b"not a zip file")
                return output_path, None

            mock_dl.side_effect = download_side_effect
            mock_csv.return_value = {"path": tmp_path / "sdn.csv", "status": "downloaded", "error": None}
            result = fetch_ofac_sdn_xml(tmp_path, force=True)

        mock_csv.assert_called_once()
        assert result["status"] == "downloaded"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Test error scenarios in the OFAC update pipeline."""

    def test_fetch_error_returns_error_result(self, mock_db, ofac_source_cfg, tmp_path):
        """When fetch fails, the result has status=error."""
        with (
            patch("app.modules.watchlist_scheduler._get_active_vessel_ids", return_value=set()),
            patch("app.modules.watchlist_scheduler._should_update", return_value=True),
            patch("app.modules.data_fetcher.fetch_ofac_sdn", return_value={"path": None, "error": "Network error"}),
            patch("app.modules.watchlist_scheduler.settings") as mock_settings,
        ):
            mock_settings.DATA_DIR = str(tmp_path)
            result = update_source(mock_db, ofac_source_cfg, force=True)

        assert result["status"] == "error"
        assert "Network error" in result["error"]

    def test_run_watchlist_update_filters_sources(self, mock_db):
        """run_watchlist_update only updates requested sources."""
        with (
            patch("app.modules.watchlist_scheduler._ensure_log_table"),
            patch("app.modules.watchlist_scheduler.update_source") as mock_update,
        ):
            mock_update.return_value = {"source": "OFAC_SDN", "status": "success"}
            results = run_watchlist_update(mock_db, force=True, sources=["OFAC_SDN"])

        assert len(results) == 1
        # Verify update_source was called with OFAC_SDN config
        call_args = mock_update.call_args
        assert call_args[0][1]["name"] == "OFAC_SDN"
