"""Tests for AIS collection source wrappers."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session


class TestCollectionSources:
    """Tests for collection_sources.py."""

    def test_get_available_sources(self):
        """Returns only enabled sources."""
        with patch("app.modules.collection_sources.settings") as mock_settings:
            mock_settings.DIGITRAFFIC_ENABLED = True
            mock_settings.KYSTVERKET_ENABLED = False
            mock_settings.BARENTSWATCH_ENABLED = False
            mock_settings.AISSTREAM_API_KEY = None
            mock_settings.COLLECT_DIGITRAFFIC_INTERVAL = 1800
            mock_settings.COLLECT_AISSTREAM_INTERVAL = 300

            from app.modules.collection_sources import get_available_sources
            sources = get_available_sources()
            assert "digitraffic" in sources
            assert "kystverket" not in sources

    def test_get_all_sources(self):
        """Returns all sources including disabled."""
        with patch("app.modules.collection_sources.settings") as mock_settings:
            mock_settings.DIGITRAFFIC_ENABLED = False
            mock_settings.KYSTVERKET_ENABLED = False
            mock_settings.BARENTSWATCH_ENABLED = False
            mock_settings.AISSTREAM_API_KEY = None
            mock_settings.COLLECT_DIGITRAFFIC_INTERVAL = 1800
            mock_settings.COLLECT_AISSTREAM_INTERVAL = 300

            from app.modules.collection_sources import get_all_sources
            sources = get_all_sources()
            assert "digitraffic" in sources
            assert "kystverket" in sources
            assert "barentswatch" in sources
            assert "aisstream" in sources

    def test_collect_from_source_dispatch(self):
        """collect_from_source calls the correct collector."""
        db = MagicMock(spec=Session)

        with patch("app.modules.collection_sources.settings") as mock_settings:
            mock_settings.DIGITRAFFIC_ENABLED = True
            mock_settings.COLLECT_DIGITRAFFIC_INTERVAL = 1800
            mock_settings.COLLECT_AISSTREAM_INTERVAL = 300

            with patch("app.modules.collection_sources._collect_digitraffic") as mock_collect:
                mock_collect.return_value = {"points_imported": 5, "vessels_seen": 2, "errors": 0}

                from app.modules.collection_sources import collect_from_source
                result = collect_from_source("digitraffic", db)
                assert mock_collect.called
                assert result["points_imported"] == 5

    def test_unknown_source_raises(self):
        """Unknown source name raises ValueError."""
        db = MagicMock(spec=Session)

        from app.modules.collection_sources import collect_from_source
        with pytest.raises(ValueError, match="Unknown source"):
            collect_from_source("nonexistent_source", db)

    def test_disabled_source_excluded(self):
        """Disabled sources are not in get_available_sources."""
        with patch("app.modules.collection_sources.settings") as mock_settings:
            mock_settings.DIGITRAFFIC_ENABLED = False
            mock_settings.KYSTVERKET_ENABLED = False
            mock_settings.BARENTSWATCH_ENABLED = False
            mock_settings.AISSTREAM_API_KEY = None
            mock_settings.COLLECT_DIGITRAFFIC_INTERVAL = 1800
            mock_settings.COLLECT_AISSTREAM_INTERVAL = 300

            from app.modules.collection_sources import get_available_sources
            sources = get_available_sources()
            assert len(sources) == 0

    def test_collect_disabled_source_returns_skipped(self):
        """Collecting from a disabled source returns skipped stats."""
        db = MagicMock(spec=Session)

        with patch("app.modules.collection_sources.settings") as mock_settings:
            mock_settings.DIGITRAFFIC_ENABLED = False
            mock_settings.COLLECT_DIGITRAFFIC_INTERVAL = 1800
            mock_settings.COLLECT_AISSTREAM_INTERVAL = 300

            from app.modules.collection_sources import collect_from_source
            result = collect_from_source("digitraffic", db)
            assert result.get("skipped") is True

    def test_source_info_fields(self):
        """SourceInfo has expected fields."""
        with patch("app.modules.collection_sources.settings") as mock_settings:
            mock_settings.DIGITRAFFIC_ENABLED = True
            mock_settings.COLLECT_DIGITRAFFIC_INTERVAL = 1800
            mock_settings.COLLECT_AISSTREAM_INTERVAL = 300
            mock_settings.KYSTVERKET_ENABLED = False
            mock_settings.BARENTSWATCH_ENABLED = False
            mock_settings.AISSTREAM_API_KEY = None

            from app.modules.collection_sources import get_available_sources
            sources = get_available_sources()
            info = sources["digitraffic"]
            assert info.name == "digitraffic"
            assert info.description
            assert info.interval_seconds == 1800
            assert info.enabled is True
            assert callable(info.collector)

    def test_collect_digitraffic_wrapper(self):
        """Digitraffic wrapper calls fetch_digitraffic_ais."""
        db = MagicMock(spec=Session)
        with patch("app.modules.digitraffic_client.fetch_digitraffic_ais") as mock_fetch:
            mock_fetch.return_value = {"points_ingested": 10, "vessels_seen": 3, "errors": 0}
            from app.modules.collection_sources import _collect_digitraffic
            result = _collect_digitraffic(db)
            assert result["points_ingested"] == 10

    def test_collect_kystverket_wrapper(self):
        """Kystverket wrapper calls stream_kystverket."""
        db = MagicMock(spec=Session)
        with patch("app.modules.kystverket_client.stream_kystverket") as mock_stream:
            mock_stream.return_value = {"points_ingested": 20, "vessels_seen": 5, "errors": 0}
            from app.modules.collection_sources import _collect_kystverket
            result = _collect_kystverket(db, duration_seconds=60)
            mock_stream.assert_called_once_with(db, duration_seconds=60)
            assert result["points_ingested"] == 20

    def test_collect_barentswatch_wrapper(self):
        """BarentsWatch wrapper calls fetch_barentswatch_tracks."""
        db = MagicMock(spec=Session)
        with patch("app.modules.barentswatch_client.fetch_barentswatch_tracks") as mock_fetch:
            mock_fetch.return_value = {"points_imported": 15, "vessels_seen": 4, "api_calls": 1, "errors": 0}
            from app.modules.collection_sources import _collect_barentswatch
            result = _collect_barentswatch(db)
            assert result["points_imported"] == 15
