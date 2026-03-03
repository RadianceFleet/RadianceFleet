"""Tests for Equasis client (equasis_client.py) and enrich_vessels_from_equasis.

Covers:
  1. EquasisClient init guards (disabled, missing credentials)
  2. _login() success and failure paths
  3. _get() lazy login, re-login on session expiry (/public/ redirect), re-login on 401
  4. search_by_imo / search_by_mmsi wiring
  5. _parse_vessel_page HTML parsing (all fields + no-data cases)
  6. enrich_vessels_from_equasis (disabled flag, enrichment, skipping, watchlist priority)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call
from typing import Optional

import pytest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.models.vessel import Vessel
from app.models.vessel_history import VesselHistory
from app.models.vessel_watchlist import VesselWatchlist

# ---------------------------------------------------------------------------
# Shared HTML helpers
# ---------------------------------------------------------------------------

def _make_vessel_html(**fields) -> str:
    """Build minimal Equasis-like HTML with the given label->value pairs."""
    rows = ""
    for label, value in fields.items():
        rows += f"<tr><td>{label}:</td><td>{value}</td></tr>\n"
    return f"<html><body><table>{rows}</table></body></html>"


_VALID_VESSEL_HTML = _make_vessel_html(
    Deadweight="65000",
    Type="Crude Oil Tanker",
    **{"Year of Build": "2003"},
    Flag="Panama",
)

_NO_SHIP_HTML = "<html><body><p>No ship found matching your search.</p></body></html>"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    """In-memory SQLite session with all tables created."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_vessel(db, mmsi="211456789", name="TEST VESSEL", **kwargs):
    v = Vessel(mmsi=mmsi, name=name, **kwargs)
    db.add(v)
    db.flush()
    return v


# ---------------------------------------------------------------------------
# Helper: create a minimal Response mock
# ---------------------------------------------------------------------------

def _make_response(url: str, status_code: int = 200, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.url = url
    resp.status_code = status_code
    resp.text = text
    resp.raise_for_status = MagicMock()  # no-op unless we configure it
    return resp


# ===========================================================================
# 1. EquasisClient init guards
# ===========================================================================

class TestEquasisClientInit:
    def test_init_raises_if_disabled(self):
        """RuntimeError when EQUASIS_SCRAPING_ENABLED is False (default)."""
        from app.modules.equasis_client import EquasisClient
        from app.config import settings

        with patch.object(settings, "EQUASIS_SCRAPING_ENABLED", False):
            with pytest.raises(RuntimeError, match="EQUASIS_SCRAPING_ENABLED"):
                EquasisClient()

    def test_init_raises_if_no_credentials(self):
        """RuntimeError when flag is True but USERNAME/PASSWORD are missing."""
        from app.modules.equasis_client import EquasisClient
        from app.config import settings

        with patch.object(settings, "EQUASIS_SCRAPING_ENABLED", True):
            with patch.object(settings, "EQUASIS_USERNAME", None):
                with patch.object(settings, "EQUASIS_PASSWORD", None):
                    with pytest.raises(RuntimeError, match="EQUASIS_USERNAME"):
                        EquasisClient()

    def test_init_raises_if_password_missing_only(self):
        """RuntimeError when username is set but password is None."""
        from app.modules.equasis_client import EquasisClient
        from app.config import settings

        with patch.object(settings, "EQUASIS_SCRAPING_ENABLED", True):
            with patch.object(settings, "EQUASIS_USERNAME", "user@example.com"):
                with patch.object(settings, "EQUASIS_PASSWORD", None):
                    with pytest.raises(RuntimeError, match="EQUASIS_USERNAME"):
                        EquasisClient()

    def test_init_succeeds_when_fully_configured(self):
        """No exception when all settings are present and enabled."""
        from app.modules.equasis_client import EquasisClient
        from app.config import settings

        with patch.object(settings, "EQUASIS_SCRAPING_ENABLED", True):
            with patch.object(settings, "EQUASIS_USERNAME", "user@example.com"):
                with patch.object(settings, "EQUASIS_PASSWORD", "secret"):
                    client = EquasisClient()
                    assert client._session is None  # session not yet established


# ===========================================================================
# 2. _login()
# ===========================================================================

class TestEquasisLogin:
    def _make_client(self):
        from app.modules.equasis_client import EquasisClient
        from app.config import settings

        with patch.object(settings, "EQUASIS_SCRAPING_ENABLED", True):
            with patch.object(settings, "EQUASIS_USERNAME", "u@x.com"):
                with patch.object(settings, "EQUASIS_PASSWORD", "pw"):
                    return EquasisClient()

    def test_login_success(self):
        """_login() succeeds when POST response URL contains /restricted/."""
        client = self._make_client()

        success_resp = _make_response("https://www.equasis.org/EquasisWeb/restricted/HomePage")
        with patch("requests.Session") as MockSession:
            mock_session_instance = MagicMock()
            mock_session_instance.post.return_value = success_resp
            MockSession.return_value = mock_session_instance

            from app.config import settings
            with patch.object(settings, "EQUASIS_USERNAME", "u@x.com"):
                with patch.object(settings, "EQUASIS_PASSWORD", "pw"):
                    client._login()

        assert client._session is mock_session_instance

    def test_login_fails_on_wrong_credentials(self):
        """_login() raises RuntimeError when POST stays on /public/ (bad credentials)."""
        client = self._make_client()

        bad_resp = _make_response("https://www.equasis.org/EquasisWeb/public/Signin?error=true")
        with patch("requests.Session") as MockSession:
            mock_session_instance = MagicMock()
            mock_session_instance.post.return_value = bad_resp
            MockSession.return_value = mock_session_instance

            from app.config import settings
            with patch.object(settings, "EQUASIS_USERNAME", "bad@x.com"):
                with patch.object(settings, "EQUASIS_PASSWORD", "wrong"):
                    with pytest.raises(RuntimeError, match="login failed"):
                        client._login()


# ===========================================================================
# 3. _get() — lazy login, session-expiry re-login, 401 re-login
# ===========================================================================

class TestEquasisGet:
    def _make_client(self):
        from app.modules.equasis_client import EquasisClient
        from app.config import settings

        with patch.object(settings, "EQUASIS_SCRAPING_ENABLED", True):
            with patch.object(settings, "EQUASIS_USERNAME", "u@x.com"):
                with patch.object(settings, "EQUASIS_PASSWORD", "pw"):
                    return EquasisClient()

    def test_get_lazy_login(self):
        """_get() calls _login() before the first GET when no session exists."""
        client = self._make_client()
        assert client._session is None

        good_resp = _make_response(
            "https://www.equasis.org/EquasisWeb/restricted/ShipInfo",
            text=_VALID_VESSEL_HTML,
        )

        login_called = []
        original_login = client._login

        def mock_login():
            login_called.append(1)
            # Set a real-ish mock session
            session_mock = MagicMock()
            session_mock.get.return_value = good_resp
            client._session = session_mock

        client._login = mock_login

        with patch("app.modules.equasis_client.time.sleep"):
            client._get("/restricted/ShipInfo", {"P_IMO": "1234567"})

        assert len(login_called) == 1

    def test_get_relogin_on_session_expiry(self):
        """_get() re-logins when response URL contains /public/ (session expired)."""
        from app.modules.equasis_client import EquasisClient
        from app.config import settings

        # We need to intercept requests.Session at the class level so both the initial
        # login (which creates a new Session()) and the subsequent calls go through our mock.

        # Responses sequence:
        #   login POST #1 -> /restricted/ (success)
        #   GET #1 -> /public/HomePage (session expired)
        #   login POST #2 -> /restricted/ (re-login success)
        #   GET #2 -> valid vessel page

        login_post_resp = _make_response("https://www.equasis.org/EquasisWeb/restricted/HomePage")
        get_expired_resp = _make_response(
            "https://www.equasis.org/EquasisWeb/public/HomePage",
        )
        get_valid_resp = _make_response(
            "https://www.equasis.org/EquasisWeb/restricted/ShipInfo",
            text=_VALID_VESSEL_HTML,
        )

        # Track how many times _login is invoked
        login_call_count = [0]

        with patch.object(settings, "EQUASIS_SCRAPING_ENABLED", True):
            with patch.object(settings, "EQUASIS_USERNAME", "u@x.com"):
                with patch.object(settings, "EQUASIS_PASSWORD", "pw"):
                    client = EquasisClient()

        # We patch requests.Session at module level so new Session() calls return our mock
        session_mock = MagicMock()
        session_mock.post.return_value = login_post_resp
        # First GET returns expired redirect; second returns valid page
        session_mock.get.side_effect = [get_expired_resp, get_valid_resp]

        original_login = client._login

        def tracking_login():
            login_call_count[0] += 1
            original_login()

        client._login = tracking_login

        with patch("requests.Session", return_value=session_mock):
            with patch("app.modules.equasis_client.time.sleep"):
                with patch.object(settings, "EQUASIS_USERNAME", "u@x.com"):
                    with patch.object(settings, "EQUASIS_PASSWORD", "pw"):
                        resp = client._get("/restricted/ShipInfo", {"P_IMO": "1234567"})

        # _login was called twice: once at lazy-init, once after session expiry
        assert login_call_count[0] == 2
        # The returned response is the valid second GET
        assert resp.text == _VALID_VESSEL_HTML

    def test_get_relogin_on_401(self):
        """_get() re-logins when response has status_code=401."""
        from app.modules.equasis_client import EquasisClient
        from app.config import settings

        login_post_resp = _make_response("https://www.equasis.org/EquasisWeb/restricted/HomePage")
        get_401_resp = _make_response(
            "https://www.equasis.org/EquasisWeb/restricted/ShipInfo",
            status_code=401,
        )
        get_valid_resp = _make_response(
            "https://www.equasis.org/EquasisWeb/restricted/ShipInfo",
            text=_VALID_VESSEL_HTML,
        )

        login_call_count = [0]

        with patch.object(settings, "EQUASIS_SCRAPING_ENABLED", True):
            with patch.object(settings, "EQUASIS_USERNAME", "u@x.com"):
                with patch.object(settings, "EQUASIS_PASSWORD", "pw"):
                    client = EquasisClient()

        session_mock = MagicMock()
        session_mock.post.return_value = login_post_resp
        session_mock.get.side_effect = [get_401_resp, get_valid_resp]

        original_login = client._login

        def tracking_login():
            login_call_count[0] += 1
            original_login()

        client._login = tracking_login

        with patch("requests.Session", return_value=session_mock):
            with patch("app.modules.equasis_client.time.sleep"):
                with patch.object(settings, "EQUASIS_USERNAME", "u@x.com"):
                    with patch.object(settings, "EQUASIS_PASSWORD", "pw"):
                        resp = client._get("/restricted/ShipInfo", {"P_IMO": "1234567"})

        assert login_call_count[0] == 2
        assert resp.text == _VALID_VESSEL_HTML


# ===========================================================================
# 4. search_by_imo / search_by_mmsi
# ===========================================================================

class TestEquasisSearch:
    def _make_client_with_session(self, session_mock):
        """Create client with a pre-configured mock session (skips login)."""
        from app.modules.equasis_client import EquasisClient
        from app.config import settings

        with patch.object(settings, "EQUASIS_SCRAPING_ENABLED", True):
            with patch.object(settings, "EQUASIS_USERNAME", "u@x.com"):
                with patch.object(settings, "EQUASIS_PASSWORD", "pw"):
                    client = EquasisClient()
        client._session = session_mock
        return client

    def test_search_by_imo_returns_parsed_dict(self):
        """search_by_imo() returns a populated dict when HTML has recognisable fields."""
        from app.modules.equasis_client import _parse_vessel_page

        html = _make_vessel_html(Deadweight="65000", Type="Crude Oil Tanker")
        resp_mock = _make_response(
            "https://www.equasis.org/EquasisWeb/restricted/ShipInfo",
            text=html,
        )
        session_mock = MagicMock()
        session_mock.get.return_value = resp_mock

        client = self._make_client_with_session(session_mock)

        with patch("app.modules.equasis_client.time.sleep"):
            result = client.search_by_imo("1234567")

        assert result is not None
        assert result["dwt"] == "65000"
        assert result["vessel_type"] == "Crude Oil Tanker"

    def test_search_by_imo_returns_none_on_no_ship_found(self):
        """search_by_imo() returns None when HTML contains 'No ship found'."""
        resp_mock = _make_response(
            "https://www.equasis.org/EquasisWeb/restricted/ShipInfo",
            text=_NO_SHIP_HTML,
        )
        session_mock = MagicMock()
        session_mock.get.return_value = resp_mock

        client = self._make_client_with_session(session_mock)

        with patch("app.modules.equasis_client.time.sleep"):
            result = client.search_by_imo("9999999")

        assert result is None

    def test_search_by_mmsi_fallback_passes_correct_params(self):
        """search_by_mmsi() calls _get with P_MMSI param and /restricted/Search path."""
        resp_mock = _make_response(
            "https://www.equasis.org/EquasisWeb/restricted/Search",
            text=_make_vessel_html(Flag="Marshall Islands"),
        )
        session_mock = MagicMock()
        session_mock.get.return_value = resp_mock

        client = self._make_client_with_session(session_mock)

        with patch("app.modules.equasis_client.time.sleep"):
            result = client.search_by_mmsi("538001234")

        # Verify the underlying session.get was called with the MMSI param
        call_args = session_mock.get.call_args
        assert "P_MMSI" in call_args[1]["params"]
        assert call_args[1]["params"]["P_MMSI"] == "538001234"
        assert result is not None
        assert result["flag"] == "Marshall Islands"

    def test_search_by_imo_returns_none_on_exception(self):
        """search_by_imo() swallows exceptions and returns None (graceful degradation)."""
        session_mock = MagicMock()
        session_mock.get.side_effect = ConnectionError("network error")

        client = self._make_client_with_session(session_mock)

        with patch("app.modules.equasis_client.time.sleep"):
            result = client.search_by_imo("1234567")

        assert result is None


# ===========================================================================
# 5. _parse_vessel_page HTML parsing
# ===========================================================================

class TestParseVesselPage:
    """Tests for the module-level _parse_vessel_page() function."""

    def test_parse_deadweight(self):
        """Deadweight label -> dwt key in result."""
        from app.modules.equasis_client import _parse_vessel_page

        html = _make_vessel_html(Deadweight="65000")
        result = _parse_vessel_page(html)
        assert result is not None
        assert result["dwt"] == "65000"

    def test_parse_vessel_type(self):
        """Type label -> vessel_type key in result."""
        from app.modules.equasis_client import _parse_vessel_page

        html = _make_vessel_html(Type="Crude Oil Tanker")
        result = _parse_vessel_page(html)
        assert result is not None
        assert result["vessel_type"] == "Crude Oil Tanker"

    def test_parse_year_built(self):
        """'Year of Build' label -> year_built key in result."""
        from app.modules.equasis_client import _parse_vessel_page

        html = _make_vessel_html(**{"Year of Build": "2003"})
        result = _parse_vessel_page(html)
        assert result is not None
        assert result["year_built"] == "2003"

    def test_parse_flag(self):
        """Flag label -> flag key in result."""
        from app.modules.equasis_client import _parse_vessel_page

        html = _make_vessel_html(Flag="Panama")
        result = _parse_vessel_page(html)
        assert result is not None
        assert result["flag"] == "Panama"

    def test_parse_ism_company(self):
        """ISM Company label -> ism_company key in result."""
        from app.modules.equasis_client import _parse_vessel_page

        html = _make_vessel_html(**{"ISM Company": "Alpha Ship Management"})
        result = _parse_vessel_page(html)
        assert result is not None
        assert result["ism_company"] == "Alpha Ship Management"

    def test_parse_gross_tonnage(self):
        """Gross Tonnage label -> gross_tonnage key in result."""
        from app.modules.equasis_client import _parse_vessel_page

        html = _make_vessel_html(**{"Gross Tonnage": "45000"})
        result = _parse_vessel_page(html)
        assert result is not None
        assert result["gross_tonnage"] == "45000"

    def test_parse_no_ship_found(self):
        """HTML containing 'No ship found' returns None."""
        from app.modules.equasis_client import _parse_vessel_page

        result = _parse_vessel_page(_NO_SHIP_HTML)
        assert result is None

    def test_parse_empty_returns_none(self):
        """HTML with no matching table rows returns None."""
        from app.modules.equasis_client import _parse_vessel_page

        html = "<html><body><p>Some page with no vessel data.</p></body></html>"
        result = _parse_vessel_page(html)
        assert result is None

    def test_parse_multiple_fields(self):
        """All four core fields parsed correctly from a single HTML blob."""
        from app.modules.equasis_client import _parse_vessel_page

        html = _make_vessel_html(
            Deadweight="65000",
            Type="Crude Oil Tanker",
            **{"Year of Build": "2003"},
            Flag="Panama",
        )
        result = _parse_vessel_page(html)
        assert result is not None
        assert result["dwt"] == "65000"
        assert result["vessel_type"] == "Crude Oil Tanker"
        assert result["year_built"] == "2003"
        assert result["flag"] == "Panama"

    def test_parse_alternative_deadweight_label(self):
        """'Dead Weight' (with space) is also recognised as dwt."""
        from app.modules.equasis_client import _parse_vessel_page

        html = _make_vessel_html(**{"Dead Weight": "80000"})
        result = _parse_vessel_page(html)
        assert result is not None
        assert result["dwt"] == "80000"

    def test_parse_no_ship_lowercase_variant(self):
        """HTML with 'no ship' (case-insensitive) also returns None."""
        from app.modules.equasis_client import _parse_vessel_page

        html = "<html><body><p>no ship data available</p></body></html>"
        result = _parse_vessel_page(html)
        assert result is None


# ===========================================================================
# 6. enrich_vessels_from_equasis (integration with in-memory SQLite)
# ===========================================================================

class TestEnrichVesselsFromEquasis:
    def test_returns_disabled_when_flag_off(self, db):
        """Returns {"disabled": True} when EQUASIS_SCRAPING_ENABLED is False."""
        from app.modules.vessel_enrichment import enrich_vessels_from_equasis
        from app.config import settings

        with patch.object(settings, "EQUASIS_SCRAPING_ENABLED", False):
            result = enrich_vessels_from_equasis(db, limit=10)

        assert result.get("disabled") is True
        assert result["enriched"] == 0

    def test_enriches_vessel_with_dwt(self, db):
        """Vessel gets DWT from Equasis; is_heuristic_dwt becomes False."""
        vessel = _make_vessel(db, "211111111", imo="1234567")
        db.commit()

        mock_client = MagicMock()
        mock_client.search_by_imo.return_value = {"dwt": "65000"}

        from app.modules.vessel_enrichment import enrich_vessels_from_equasis
        # EquasisClient is imported locally inside enrich_vessels_from_equasis,
        # so we patch it at its definition location.
        with patch("app.modules.equasis_client.EquasisClient", return_value=mock_client):
            result = enrich_vessels_from_equasis(db, limit=10)

        db.refresh(vessel)
        assert result["enriched"] == 1
        assert vessel.deadweight == 65000.0
        assert vessel.is_heuristic_dwt is False

    def test_enriches_vessel_creates_vessel_history(self, db):
        """DWT enrichment creates a VesselHistory record with source='equasis_enrichment'."""
        vessel = _make_vessel(db, "211111112", imo="1234568")
        db.commit()

        mock_client = MagicMock()
        mock_client.search_by_imo.return_value = {"dwt": "65000"}

        from app.modules.vessel_enrichment import enrich_vessels_from_equasis
        with patch("app.modules.equasis_client.EquasisClient", return_value=mock_client):
            enrich_vessels_from_equasis(db, limit=10)

        hist = db.query(VesselHistory).filter(
            VesselHistory.vessel_id == vessel.vessel_id,
            VesselHistory.field_changed == "deadweight",
            VesselHistory.source == "equasis_enrichment",
        ).first()
        assert hist is not None
        assert hist.new_value == "65000.0"

    def test_skips_vessel_when_no_data(self, db):
        """When client returns None, stats['skipped'] increments."""
        _make_vessel(db, "211111113", imo="1234569")
        db.commit()

        mock_client = MagicMock()
        mock_client.search_by_imo.return_value = None
        # No mmsi set so no MMSI fallback

        from app.modules.vessel_enrichment import enrich_vessels_from_equasis
        with patch("app.modules.equasis_client.EquasisClient", return_value=mock_client):
            result = enrich_vessels_from_equasis(db, limit=10)

        assert result["enriched"] == 0
        assert result["skipped"] == 1

    def test_watchlisted_sorted_first(self, db):
        """Watchlisted vessel is enriched before non-watchlisted vessel."""
        # Create two vessels — v_normal first (lower vessel_id), then v_watch
        v_normal = _make_vessel(db, "211000001", name="NORMAL", imo="1111111")
        v_watch = _make_vessel(db, "211000002", name="WATCHLISTED", imo="2222222")
        db.commit()

        # Add watchlist entry for v_watch
        wl = VesselWatchlist(
            vessel_id=v_watch.vessel_id,
            watchlist_source="OFAC",
            is_active=True,
        )
        db.add(wl)
        db.commit()

        queried_order = []
        mock_client = MagicMock()

        def record_imo(imo):
            queried_order.append(imo)
            return None  # Return None so all are skipped

        mock_client.search_by_imo.side_effect = record_imo

        from app.modules.vessel_enrichment import enrich_vessels_from_equasis
        with patch("app.modules.equasis_client.EquasisClient", return_value=mock_client):
            enrich_vessels_from_equasis(db, limit=10)

        # The watchlisted vessel's IMO should appear FIRST
        assert queried_order[0] == v_watch.imo
        assert queried_order[1] == v_normal.imo

    def test_commit_only_when_enriched(self, db):
        """db.commit() is NOT called when no vessel is enriched."""
        _make_vessel(db, "211000003", imo="3333333")
        db.commit()

        mock_client = MagicMock()
        mock_client.search_by_imo.return_value = None

        from app.modules.vessel_enrichment import enrich_vessels_from_equasis

        db_mock = MagicMock(wraps=db)

        with patch("app.modules.equasis_client.EquasisClient", return_value=mock_client):
            result = enrich_vessels_from_equasis(db_mock, limit=10)

        assert result["enriched"] == 0
        db_mock.commit.assert_not_called()

    def test_mmsi_fallback_when_imo_search_fails(self, db):
        """Falls back to search_by_mmsi when search_by_imo returns None and vessel has MMSI."""
        vessel = _make_vessel(db, "211999999", imo="9999999")
        db.commit()

        mock_client = MagicMock()
        mock_client.search_by_imo.return_value = None
        mock_client.search_by_mmsi.return_value = {"flag": "Liberia"}

        from app.modules.vessel_enrichment import enrich_vessels_from_equasis
        with patch("app.modules.equasis_client.EquasisClient", return_value=mock_client):
            result = enrich_vessels_from_equasis(db, limit=10)

        db.refresh(vessel)
        assert result["enriched"] == 1
        assert vessel.flag == "Liberia"
        mock_client.search_by_mmsi.assert_called_once_with(vessel.mmsi)
