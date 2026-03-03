"""Tests for data quality features: digitraffic downsample + feed outage source-awareness."""
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

import pytest


class TestDigitraficDownsample:
    """Test the 30-minute downsample guard in digitraffic_client."""

    @patch("app.modules.digitraffic_client.httpx.Client")
    def test_downsample_30min_skips_recent(self, mock_httpx_cls):
        """Rapid polls skip if <30min since last point for same vessel."""
        from app.modules.digitraffic_client import fetch_digitraffic_ais

        now = datetime.utcnow()
        ts_epoch_ms = int(now.timestamp() * 1000)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "features": [
                {
                    "properties": {"mmsi": "211000001", "sog": 100, "cog": 1800,
                                   "heading": 180, "timestampExternal": ts_epoch_ms},
                    "geometry": {"coordinates": [24.0, 60.0]},
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_httpx_cls.return_value = mock_client

        db = MagicMock()
        # Vessel exists
        vessel_mock = MagicMock()
        vessel_mock.vessel_id = 1
        db.query.return_value.filter.return_value.first.return_value = vessel_mock

        # Last digitraffic point: 10 minutes ago (< 30 min -> should downsample)
        recent_ts = now - timedelta(minutes=10)
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (recent_ts,)

        with patch("app.modules.digitraffic_client.settings") as mock_settings:
            mock_settings.DIGITRAFFIC_ENABLED = True
            result = fetch_digitraffic_ais(db)

        assert result["downsampled"] >= 1
        assert result["points_ingested"] == 0

    @patch("app.modules.digitraffic_client.httpx.Client")
    def test_downsample_allows_after_30min(self, mock_httpx_cls):
        """31-min gap => point stored."""
        from app.modules.digitraffic_client import fetch_digitraffic_ais

        now = datetime.utcnow()
        ts_epoch_ms = int(now.timestamp() * 1000)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "features": [
                {
                    "properties": {"mmsi": "211000001", "sog": 100, "cog": 1800,
                                   "heading": 180, "timestampExternal": ts_epoch_ms},
                    "geometry": {"coordinates": [24.0, 60.0]},
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_httpx_cls.return_value = mock_client

        db = MagicMock()
        vessel_mock = MagicMock()
        vessel_mock.vessel_id = 1
        db.query.return_value.filter.return_value.first.return_value = vessel_mock

        # Last point: 31 minutes ago (>= 30 min -> should allow)
        old_ts = now - timedelta(minutes=31)
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (old_ts,)

        with patch("app.modules.digitraffic_client.settings") as mock_settings:
            mock_settings.DIGITRAFFIC_ENABLED = True
            result = fetch_digitraffic_ais(db)

        assert result["downsampled"] == 0

    @patch("app.modules.digitraffic_client.httpx.Client")
    def test_downsample_first_point_always_stored(self, mock_httpx_cls):
        """No previous point => point is stored (not downsampled)."""
        from app.modules.digitraffic_client import fetch_digitraffic_ais

        now = datetime.utcnow()
        ts_epoch_ms = int(now.timestamp() * 1000)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "features": [
                {
                    "properties": {"mmsi": "211000001", "sog": 100, "cog": 1800,
                                   "heading": 180, "timestampExternal": ts_epoch_ms},
                    "geometry": {"coordinates": [24.0, 60.0]},
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_httpx_cls.return_value = mock_client

        db = MagicMock()
        vessel_mock = MagicMock()
        vessel_mock.vessel_id = 1
        db.query.return_value.filter.return_value.first.return_value = vessel_mock
        # No previous digitraffic point
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        with patch("app.modules.digitraffic_client.settings") as mock_settings:
            mock_settings.DIGITRAFFIC_ENABLED = True
            result = fetch_digitraffic_ais(db)

        assert result["downsampled"] == 0

    @patch("app.modules.digitraffic_client.httpx.Client")
    def test_downsample_stats_counter(self, mock_httpx_cls):
        """Downsampled count is tracked in result stats."""
        from app.modules.digitraffic_client import fetch_digitraffic_ais

        now = datetime.utcnow()
        ts_epoch_ms = int(now.timestamp() * 1000)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "features": [
                {
                    "properties": {"mmsi": "211000001", "sog": 100, "cog": 1800,
                                   "heading": 180, "timestampExternal": ts_epoch_ms},
                    "geometry": {"coordinates": [24.0, 60.0]},
                },
                {
                    "properties": {"mmsi": "211000002", "sog": 100, "cog": 1800,
                                   "heading": 180, "timestampExternal": ts_epoch_ms},
                    "geometry": {"coordinates": [25.0, 61.0]},
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_httpx_cls.return_value = mock_client

        db = MagicMock()
        vessel_mock = MagicMock()
        vessel_mock.vessel_id = 1
        db.query.return_value.filter.return_value.first.return_value = vessel_mock

        # All vessels have recent points -> all downsampled
        recent_ts = now - timedelta(minutes=5)
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (recent_ts,)

        with patch("app.modules.digitraffic_client.settings") as mock_settings:
            mock_settings.DIGITRAFFIC_ENABLED = True
            result = fetch_digitraffic_ais(db)

        assert "downsampled" in result
        assert result["downsampled"] >= 1

    def test_disabled_returns_zero(self):
        """Disabled digitraffic returns zero stats."""
        from app.modules.digitraffic_client import fetch_digitraffic_ais

        db = MagicMock()
        with patch("app.modules.digitraffic_client.settings") as mock_settings:
            mock_settings.DIGITRAFFIC_ENABLED = False
            result = fetch_digitraffic_ais(db)

        assert result["points_ingested"] == 0


class TestFeedOutageSourceAware:
    """Test source-aware outage grouping in feed_outage_detector."""

    def test_detect_dominant_source_single_source(self):
        """Digitraffic-only cluster => source detected."""
        from app.modules.feed_outage_detector import _detect_dominant_source

        gaps = []
        for i in range(10):
            gap = MagicMock()
            gap.source = "digitraffic"
            gaps.append(gap)

        result = _detect_dominant_source(gaps)
        assert result == "digitraffic"

    def test_detect_dominant_source_mixed(self):
        """Multi-source cluster => no dominant source."""
        from app.modules.feed_outage_detector import _detect_dominant_source

        gaps = []
        for i in range(5):
            gap = MagicMock()
            gap.source = "digitraffic"
            gaps.append(gap)
        for i in range(5):
            gap = MagicMock()
            gap.source = "aisstream"
            gaps.append(gap)

        result = _detect_dominant_source(gaps)
        assert result is None

    def test_detect_dominant_source_above_threshold(self):
        """Source at exactly 81% is detected."""
        from app.modules.feed_outage_detector import _detect_dominant_source

        gaps = []
        # 9 digitraffic + 2 aisstream = 9/11 = 81.8%
        for i in range(9):
            gap = MagicMock()
            gap.source = "digitraffic"
            gaps.append(gap)
        for i in range(2):
            gap = MagicMock()
            gap.source = "aisstream"
            gaps.append(gap)

        result = _detect_dominant_source(gaps)
        assert result == "digitraffic"

    def test_detect_dominant_source_at_80_pct(self):
        """Source at exactly 80% is NOT detected (needs >80%)."""
        from app.modules.feed_outage_detector import _detect_dominant_source

        gaps = []
        # 8 digitraffic + 2 aisstream = 8/10 = 80%
        for i in range(8):
            gap = MagicMock()
            gap.source = "digitraffic"
            gaps.append(gap)
        for i in range(2):
            gap = MagicMock()
            gap.source = "aisstream"
            gaps.append(gap)

        result = _detect_dominant_source(gaps)
        assert result is None

    def test_detect_dominant_source_no_source_field(self):
        """Gaps with no source field return None."""
        from app.modules.feed_outage_detector import _detect_dominant_source

        gaps = []
        for i in range(5):
            gap = MagicMock()
            gap.source = None
            gaps.append(gap)

        result = _detect_dominant_source(gaps)
        assert result is None

    def test_detect_dominant_source_empty(self):
        """Empty list returns None."""
        from app.modules.feed_outage_detector import _detect_dominant_source

        result = _detect_dominant_source([])
        assert result is None

    def test_feed_outage_source_outage_count(self):
        """Full detect_feed_outages with source-specific cluster returns source_outages_detected."""
        from app.modules.feed_outage_detector import detect_feed_outages

        db = MagicMock()

        # Create mock gaps from a single source (digitraffic), all in the same corridor/window
        # Need 9 vessels to exceed _MIN_VESSELS_FOR_OUTAGE (8)
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        gaps = []
        for i in range(9):
            gap = MagicMock()
            gap.vessel_id = i + 1
            gap.gap_start_utc = now
            gap.gap_end_utc = now + timedelta(hours=1)
            gap.corridor_id = 1
            gap.source = "digitraffic"
            gap.risk_score = 0
            gap.is_feed_outage = False
            gap.coverage_quality = None
            gaps.append(gap)

        db.query.return_value.filter.return_value.all.return_value = gaps
        # No high-risk vessels
        db.query.return_value.filter.return_value.distinct.return_value.all.return_value = []

        with patch("app.modules.feed_outage_detector.settings") as mock_settings, \
             patch("app.modules.feed_outage_detector._has_evasion_signals", return_value=False), \
             patch("app.modules.feed_outage_detector._get_threshold", return_value=8):
            mock_settings.FEED_OUTAGE_DETECTION_ENABLED = True
            result = detect_feed_outages(db)

        assert result["source_outages_detected"] >= 1
        assert result["gaps_marked"] >= 1

    def test_feed_outage_mixed_sources_no_source_outage(self):
        """Mixed-source cluster has 0 source_outages_detected."""
        from app.modules.feed_outage_detector import detect_feed_outages

        db = MagicMock()

        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        gaps = []
        sources = ["digitraffic", "aisstream", "kystverket", "digitraffic", "aisstream", "kystverket"]
        for i, src in enumerate(sources):
            gap = MagicMock()
            gap.vessel_id = i + 1
            gap.gap_start_utc = now
            gap.gap_end_utc = now + timedelta(hours=1)
            gap.corridor_id = 1
            gap.source = src
            gap.risk_score = 0
            gap.is_feed_outage = False
            gap.coverage_quality = None
            gaps.append(gap)

        db.query.return_value.filter.return_value.all.return_value = gaps
        db.query.return_value.filter.return_value.distinct.return_value.all.return_value = []

        with patch("app.modules.feed_outage_detector.settings") as mock_settings, \
             patch("app.modules.feed_outage_detector._has_evasion_signals", return_value=False), \
             patch("app.modules.feed_outage_detector._get_threshold", return_value=5):
            mock_settings.FEED_OUTAGE_DETECTION_ENABLED = True
            result = detect_feed_outages(db)

        assert result["source_outages_detected"] == 0

    def test_feed_outage_disabled_returns_zeros(self):
        """Disabled feed outage returns zero stats."""
        from app.modules.feed_outage_detector import detect_feed_outages

        db = MagicMock()
        with patch("app.modules.feed_outage_detector.settings") as mock_settings:
            mock_settings.FEED_OUTAGE_DETECTION_ENABLED = False
            result = detect_feed_outages(db)

        assert result["gaps_checked"] == 0
        assert result["outages_detected"] == 0
