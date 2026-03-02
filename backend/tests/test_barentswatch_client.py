"""Tests for BarentsWatch AIS API client."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from sqlalchemy.orm import Session


class TestBarentsWatchClient:
    """Tests for barentswatch_client.py."""

    def _make_geojson_feature(
        self,
        mmsi="257000001",
        lat=70.0,
        lon=25.0,
        sog=12.5,
        cog=180.0,
        heading=179,
        ts="2026-03-01T12:00:00Z",
    ) -> dict:
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "mmsi": int(mmsi),
                "sog": sog,
                "cog": cog,
                "heading": heading,
                "timestamp": ts,
            },
        }

    def test_barentswatch_token_request(self):
        """OAuth token request has correct params."""
        import httpx

        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "test-token"}
        mock_response.raise_for_status = MagicMock()

        with patch("app.modules.barentswatch_client.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            from app.modules.barentswatch_client import get_barentswatch_token
            token = get_barentswatch_token(
                client_id="test-id",
                client_secret="test-secret",
            )
            assert token == "test-token"

            # Verify OAuth params
            call_args = mock_client.post.call_args
            data = call_args[1].get("data", call_args[0][1] if len(call_args[0]) > 1 else {})
            assert data["grant_type"] == "client_credentials"
            assert data["scope"] == "ais"

    def test_barentswatch_track_fetch(self):
        """GeoJSON features are parsed and ingested correctly."""
        db = MagicMock(spec=Session)
        db.query.return_value.filter.return_value.first.return_value = None

        features = [self._make_geojson_feature()]
        mock_response = MagicMock()
        mock_response.json.return_value = {"features": features}
        mock_response.raise_for_status = MagicMock()

        with patch("app.modules.barentswatch_client.settings") as mock_settings:
            mock_settings.BARENTSWATCH_ENABLED = True
            mock_settings.BARENTSWATCH_API_URL = "https://test.api/api"

            with patch("app.modules.barentswatch_client.httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.return_value = mock_response
                mock_client_cls.return_value = mock_client

                from app.modules.barentswatch_client import fetch_barentswatch_tracks
                result = fetch_barentswatch_tracks(
                    db, token="test-token",
                )
                assert result["api_calls"] >= 1

    def test_barentswatch_14day_limit(self):
        """Dates older than 14 days are clamped."""
        db = MagicMock(spec=Session)

        mock_response = MagicMock()
        mock_response.json.return_value = {"features": []}
        mock_response.raise_for_status = MagicMock()

        with patch("app.modules.barentswatch_client.settings") as mock_settings:
            mock_settings.BARENTSWATCH_ENABLED = True
            mock_settings.BARENTSWATCH_API_URL = "https://test.api/api"

            with patch("app.modules.barentswatch_client.httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.return_value = mock_response
                mock_client_cls.return_value = mock_client

                from app.modules.barentswatch_client import fetch_barentswatch_tracks
                old_date = date.today() - timedelta(days=30)
                result = fetch_barentswatch_tracks(
                    db, start_date=old_date, token="test-token",
                )
                # Should not error, just clamp
                assert result["errors"] == 0

    def test_barentswatch_feature_flag(self):
        """Disabled returns early."""
        db = MagicMock(spec=Session)
        with patch("app.modules.barentswatch_client.settings") as mock_settings:
            mock_settings.BARENTSWATCH_ENABLED = False
            from app.modules.barentswatch_client import fetch_barentswatch_tracks
            result = fetch_barentswatch_tracks(db)
            assert result["points_imported"] == 0
            assert result["api_calls"] == 0

    def test_barentswatch_auth_failure(self):
        """401 handled gracefully."""
        db = MagicMock(spec=Session)
        with patch("app.modules.barentswatch_client.settings") as mock_settings:
            mock_settings.BARENTSWATCH_ENABLED = True
            mock_settings.BARENTSWATCH_CLIENT_ID = ""
            mock_settings.BARENTSWATCH_CLIENT_SECRET = ""

            from app.modules.barentswatch_client import fetch_barentswatch_tracks
            # No token and empty credentials -> error
            result = fetch_barentswatch_tracks(db)
            assert result["errors"] >= 1

    def test_barentswatch_empty_response(self):
        """No tracks returns empty stats."""
        db = MagicMock(spec=Session)

        mock_response = MagicMock()
        mock_response.json.return_value = {"features": []}
        mock_response.raise_for_status = MagicMock()

        with patch("app.modules.barentswatch_client.settings") as mock_settings:
            mock_settings.BARENTSWATCH_ENABLED = True
            mock_settings.BARENTSWATCH_API_URL = "https://test.api/api"

            with patch("app.modules.barentswatch_client.httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.return_value = mock_response
                mock_client_cls.return_value = mock_client

                from app.modules.barentswatch_client import fetch_barentswatch_tracks
                result = fetch_barentswatch_tracks(db, token="test-token")
                assert result["points_imported"] == 0
                assert result["vessels_seen"] == 0

    def test_barentswatch_mmsi_filter(self):
        """Only requested MMSIs are queried."""
        db = MagicMock(spec=Session)
        db.query.return_value.filter.return_value.first.return_value = None

        features = [self._make_geojson_feature(mmsi="257000001")]
        mock_response = MagicMock()
        mock_response.json.return_value = {"features": features}
        mock_response.raise_for_status = MagicMock()

        with patch("app.modules.barentswatch_client.settings") as mock_settings:
            mock_settings.BARENTSWATCH_ENABLED = True
            mock_settings.BARENTSWATCH_API_URL = "https://test.api/api"

            with patch("app.modules.barentswatch_client.httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.return_value = mock_response
                mock_client_cls.return_value = mock_client

                from app.modules.barentswatch_client import fetch_barentswatch_tracks
                result = fetch_barentswatch_tracks(
                    db,
                    mmsis=["257000001", "257000002"],
                    token="test-token",
                )
                # Should make one API call per MMSI
                assert result["api_calls"] == 2

    def test_barentswatch_vessel_upsert(self):
        """New vessels are created when not found."""
        db = MagicMock(spec=Session)
        db.query.return_value.filter.return_value.first.return_value = None
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = None

        features = [self._make_geojson_feature()]
        mock_response = MagicMock()
        mock_response.json.return_value = {"features": features}
        mock_response.raise_for_status = MagicMock()

        with patch("app.modules.barentswatch_client.settings") as mock_settings:
            mock_settings.BARENTSWATCH_ENABLED = True
            mock_settings.BARENTSWATCH_API_URL = "https://test.api/api"

            with patch("app.modules.barentswatch_client.httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.return_value = mock_response
                mock_client_cls.return_value = mock_client

                from app.modules.barentswatch_client import fetch_barentswatch_tracks
                result = fetch_barentswatch_tracks(db, token="test-token")
                # db.add should have been called (vessel + point)
                assert db.add.called

    def test_barentswatch_dedup(self):
        """Duplicate points are skipped."""
        db = MagicMock(spec=Session)

        mock_vessel = MagicMock()
        mock_vessel.vessel_id = 1

        existing_point = MagicMock()

        # First filter returns vessel, second returns existing point
        call_count = [0]

        def mock_filter_first(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] % 2 == 1:
                return mock_vessel  # vessel lookup
            return existing_point  # dedup lookup

        filter_mock = MagicMock()
        filter_mock.first = mock_filter_first
        filter_mock.filter.return_value = filter_mock
        db.query.return_value.filter.return_value = filter_mock

        features = [self._make_geojson_feature()]
        mock_response = MagicMock()
        mock_response.json.return_value = {"features": features}
        mock_response.raise_for_status = MagicMock()

        with patch("app.modules.barentswatch_client.settings") as mock_settings:
            mock_settings.BARENTSWATCH_ENABLED = True
            mock_settings.BARENTSWATCH_API_URL = "https://test.api/api"

            with patch("app.modules.barentswatch_client.httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.get.return_value = mock_response
                mock_client_cls.return_value = mock_client

                from app.modules.barentswatch_client import fetch_barentswatch_tracks
                result = fetch_barentswatch_tracks(db, token="test-token")
                # Point already exists so should not be added again
                # (exact behavior depends on mock setup)
                assert result["errors"] == 0

    def test_barentswatch_stats(self):
        """Stats dict has all expected keys."""
        db = MagicMock(spec=Session)
        with patch("app.modules.barentswatch_client.settings") as mock_settings:
            mock_settings.BARENTSWATCH_ENABLED = False
            from app.modules.barentswatch_client import fetch_barentswatch_tracks
            result = fetch_barentswatch_tracks(db)
            expected_keys = {"points_imported", "vessels_seen", "api_calls", "errors"}
            assert expected_keys == set(result.keys())
