"""Tests for P&I Club Insurance Verification via Equasis."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.modules.pi_verification import IG_PI_CLUBS, _is_ig_club, check_pi_coverage

# ── _is_ig_club unit tests ──────────────────────────────────────────────────


class TestIsIgClub:
    def test_exact_match(self):
        assert _is_ig_club("Gard") is True

    def test_case_insensitive(self):
        assert _is_ig_club("BRITANNIA") is True
        assert _is_ig_club("gard") is True

    def test_partial_match_long_name(self):
        assert _is_ig_club("THE BRITANNIA STEAM SHIP INSURANCE ASSOCIATION") is True

    def test_standard_club_variant(self):
        assert _is_ig_club("The Standard Club Ltd") is True

    def test_unknown_club(self):
        assert _is_ig_club("Oceanus Mutual Insurance") is False

    def test_all_ig_clubs_recognized(self):
        for club in IG_PI_CLUBS:
            assert _is_ig_club(club) is True


# ── check_pi_coverage tests ────────────────────────────────────────────────


def _make_vessel(imo: str | None = "1234567") -> SimpleNamespace:
    return SimpleNamespace(imo=imo, vessel_id="v-1")


class TestCheckPiCoverage:
    """Tests for check_pi_coverage()."""

    def test_disabled_returns_none(self):
        """When EQUASIS_SCRAPING_ENABLED=false, returns None immediately."""
        db = MagicMock()
        vessel = _make_vessel()
        with patch("app.modules.pi_verification.settings") as mock_settings:
            mock_settings.EQUASIS_SCRAPING_ENABLED = False
            result = check_pi_coverage(db, vessel)
        assert result is None

    def test_no_imo_returns_none(self):
        """Vessel without IMO should return None."""
        db = MagicMock()
        vessel = _make_vessel(imo=None)
        with patch("app.modules.pi_verification.settings") as mock_settings:
            mock_settings.EQUASIS_SCRAPING_ENABLED = True
            result = check_pi_coverage(db, vessel)
        assert result is None

    def test_empty_imo_returns_none(self):
        """Vessel with empty string IMO should return None."""
        db = MagicMock()
        vessel = _make_vessel(imo="")
        with patch("app.modules.pi_verification.settings") as mock_settings:
            mock_settings.EQUASIS_SCRAPING_ENABLED = True
            result = check_pi_coverage(db, vessel)
        assert result is None

    def test_ig_club_found(self):
        """Known IG club returns found=True."""
        db = MagicMock()
        vessel = _make_vessel()
        mock_client = MagicMock()
        mock_client.get_pi_info.return_value = {
            "club_name": "Gard P&I (Bermuda) Ltd",
            "effective_date": "2024-01-15",
        }
        with (
            patch("app.modules.pi_verification.settings") as mock_settings,
            patch("app.modules.equasis_client.EquasisClient", return_value=mock_client),
        ):
            mock_settings.EQUASIS_SCRAPING_ENABLED = True
            result = check_pi_coverage(db, vessel)
        assert result is not None
        assert result["found"] is True
        assert result["club_name"] == "Gard P&I (Bermuda) Ltd"
        assert result["source"] == "equasis"

    def test_non_ig_club_found_false(self):
        """Unknown club returns found=False but still reports club_name."""
        db = MagicMock()
        vessel = _make_vessel()
        mock_client = MagicMock()
        mock_client.get_pi_info.return_value = {
            "club_name": "Oceanus Mutual Insurance",
            "effective_date": None,
        }
        with (
            patch("app.modules.pi_verification.settings") as mock_settings,
            patch("app.modules.equasis_client.EquasisClient", return_value=mock_client),
        ):
            mock_settings.EQUASIS_SCRAPING_ENABLED = True
            result = check_pi_coverage(db, vessel)
        assert result is not None
        assert result["found"] is False
        assert result["club_name"] == "Oceanus Mutual Insurance"

    def test_no_pi_data_from_equasis(self):
        """Equasis returns None for P&I info -> found=False, club_name=None."""
        db = MagicMock()
        vessel = _make_vessel()
        mock_client = MagicMock()
        mock_client.get_pi_info.return_value = None
        with (
            patch("app.modules.pi_verification.settings") as mock_settings,
            patch("app.modules.equasis_client.EquasisClient", return_value=mock_client),
        ):
            mock_settings.EQUASIS_SCRAPING_ENABLED = True
            result = check_pi_coverage(db, vessel)
        assert result is not None
        assert result["found"] is False
        assert result["club_name"] is None

    def test_equasis_runtime_error_returns_none(self):
        """EquasisClient raising RuntimeError (disabled) returns None gracefully."""
        db = MagicMock()
        vessel = _make_vessel()
        with (
            patch("app.modules.pi_verification.settings") as mock_settings,
            patch(
                "app.modules.equasis_client.EquasisClient",
                side_effect=RuntimeError("Equasis scraping is disabled"),
            ),
        ):
            mock_settings.EQUASIS_SCRAPING_ENABLED = True
            result = check_pi_coverage(db, vessel)
        assert result is None

    def test_equasis_connection_error_returns_none(self):
        """Network failure returns None gracefully."""
        db = MagicMock()
        vessel = _make_vessel()
        mock_client = MagicMock()
        mock_client.get_pi_info.side_effect = ConnectionError("timeout")
        with (
            patch("app.modules.pi_verification.settings") as mock_settings,
            patch("app.modules.equasis_client.EquasisClient", return_value=mock_client),
        ):
            mock_settings.EQUASIS_SCRAPING_ENABLED = True
            result = check_pi_coverage(db, vessel)
        assert result is None

    def test_ig_club_case_insensitive_match(self):
        """Case-insensitive matching for IG club names."""
        db = MagicMock()
        vessel = _make_vessel()
        mock_client = MagicMock()
        mock_client.get_pi_info.return_value = {
            "club_name": "THE SKULD MUTUAL PROTECTION AND INDEMNITY ASSOCIATION",
            "effective_date": "2023-06-01",
        }
        with (
            patch("app.modules.pi_verification.settings") as mock_settings,
            patch("app.modules.equasis_client.EquasisClient", return_value=mock_client),
        ):
            mock_settings.EQUASIS_SCRAPING_ENABLED = True
            result = check_pi_coverage(db, vessel)
        assert result is not None
        assert result["found"] is True

    def test_empty_club_name_returns_not_found(self):
        """P&I data with empty club_name -> found=False."""
        db = MagicMock()
        vessel = _make_vessel()
        mock_client = MagicMock()
        mock_client.get_pi_info.return_value = {
            "club_name": "",
            "effective_date": None,
        }
        with (
            patch("app.modules.pi_verification.settings") as mock_settings,
            patch("app.modules.equasis_client.EquasisClient", return_value=mock_client),
        ):
            mock_settings.EQUASIS_SCRAPING_ENABLED = True
            result = check_pi_coverage(db, vessel)
        assert result is not None
        assert result["found"] is False
        assert result["club_name"] is None

    def test_west_of_england_match(self):
        """'West of England' should match the 'West' IG club."""
        db = MagicMock()
        vessel = _make_vessel()
        mock_client = MagicMock()
        mock_client.get_pi_info.return_value = {
            "club_name": "West of England Ship Owners Mutual Insurance Association",
            "effective_date": "2025-01-01",
        }
        with (
            patch("app.modules.pi_verification.settings") as mock_settings,
            patch("app.modules.equasis_client.EquasisClient", return_value=mock_client),
        ):
            mock_settings.EQUASIS_SCRAPING_ENABLED = True
            result = check_pi_coverage(db, vessel)
        assert result is not None
        assert result["found"] is True


# ── _parse_pi_section tests ────────────────────────────────────────────────


class TestParsePiSection:
    def test_parse_pi_from_html(self):
        from app.modules.equasis_client import _parse_pi_section

        html = """
        <table>
        <tr>
            <td>12345</td>
            <td>ISM Manager</td>
            <td>SomeCo</td>
            <td>PA</td>
            <td>2023-01-01</td>
        </tr>
        <tr>
            <td>67890</td>
            <td>P&amp;I Club</td>
            <td>Gard P&amp;I (Bermuda) Ltd</td>
            <td>NO</td>
            <td>2024-02-15</td>
        </tr>
        </table>
        """
        result = _parse_pi_section(html)
        assert result is not None
        assert result["club_name"] == "Gard P&I (Bermuda) Ltd"
        assert result["effective_date"] == "2024-02-15"

    def test_parse_no_pi_section(self):
        from app.modules.equasis_client import _parse_pi_section

        html = """
        <table>
        <tr>
            <td>12345</td>
            <td>ISM Manager</td>
            <td>SomeCo</td>
            <td>PA</td>
            <td>2023-01-01</td>
        </tr>
        </table>
        """
        result = _parse_pi_section(html)
        assert result is None
