"""Tests for Phase A4-5: bunkering vessel exclusion and transliteration normalization."""
from unittest.mock import MagicMock, patch

import pytest


# ── Bunkering exclusion tests ────────────────────────────────────────────────


class TestLoadBunkeringExclusions:
    def setup_method(self):
        """Reset the module-level cache before each test."""
        import app.modules.sts_detector as mod
        mod._BUNKERING_EXCLUSIONS = None

    def test_load_bunkering_exclusions(self):
        """Verify the YAML loads and returns a non-empty set."""
        from app.modules.sts_detector import _load_bunkering_exclusions
        result = _load_bunkering_exclusions()
        assert isinstance(result, set)
        assert len(result) > 0
        # Check that known bunkering MMSIs are present
        assert "470581000" in result  # Cockett Bunker 1 (Fujairah)
        assert "563046700" in result  # GP Global Bunker (Singapore)
        assert "236118000" in result  # Aegean Bunker (Gibraltar)

    def test_bunkering_vessel_excluded_from_sts(self):
        """Mock a vessel with MMSI in exclusion list, verify _is_bunkering_vessel returns True."""
        from app.modules.sts_detector import _is_bunkering_vessel

        db = MagicMock()
        vessel = MagicMock()
        vessel.mmsi = "470581000"  # Matches a bunkering vessel in the YAML (Cockett Bunker 1)
        db.query.return_value.filter.return_value.first.return_value = vessel

        assert _is_bunkering_vessel(db, 1) is True

    def test_non_bunkering_vessel_not_excluded(self):
        """Verify normal vessel passes through."""
        from app.modules.sts_detector import _is_bunkering_vessel

        db = MagicMock()
        vessel = MagicMock()
        vessel.mmsi = "311000999"  # Not in the exclusion list
        db.query.return_value.filter.return_value.first.return_value = vessel

        assert _is_bunkering_vessel(db, 1) is False

    def test_missing_bunkering_yaml_returns_empty(self):
        """Verify graceful handling when YAML file doesn't exist."""
        import app.modules.sts_detector as mod
        mod._BUNKERING_EXCLUSIONS = None

        with patch("app.modules.sts_detector.Path") as mock_path_cls:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = False
            # Path(__file__).resolve().parent.parent.parent.parent / "config" / "bunkering_exclusions.yaml"
            mock_path_cls.return_value.resolve.return_value.parent.parent.parent.parent.__truediv__.return_value.__truediv__.return_value = mock_path_instance

            result = mod._load_bunkering_exclusions()
            assert isinstance(result, set)
            assert len(result) == 0


# ── Transliteration normalization tests ──────────────────────────────────────


class TestNormalizeName:
    def test_normalize_name_cyrillic(self):
        """Cyrillic vessel names should transliterate to ASCII."""
        from app.modules.watchlist_loader import _normalize_name
        result = _normalize_name("\u0411\u0410\u041b\u0422\u0418\u0419\u0421\u041a")
        # unidecode transliterates Cyrillic to ASCII
        assert result.isascii()
        assert "BALTIISK" in result or "BALTIYSK" in result or "BALTIISK" == result

    def test_normalize_name_accented(self):
        """Accented Latin characters should be normalized."""
        from app.modules.watchlist_loader import _normalize_name
        result = _normalize_name("S\u00e3o Tom\u00e9")
        assert result == "SAO TOME"

    def test_normalize_name_already_ascii(self):
        """ASCII names should pass through unchanged (uppercased)."""
        from app.modules.watchlist_loader import _normalize_name
        assert _normalize_name("EAGLE S") == "EAGLE S"

    def test_normalize_name_empty(self):
        """Empty string should return empty string."""
        from app.modules.watchlist_loader import _normalize_name
        assert _normalize_name("") == ""

    def test_normalize_name_lowercase(self):
        """Lowercase input should be uppercased."""
        from app.modules.watchlist_loader import _normalize_name
        assert _normalize_name("tanker one") == "TANKER ONE"

    def test_normalize_name_whitespace(self):
        """Leading/trailing whitespace should be stripped."""
        from app.modules.watchlist_loader import _normalize_name
        assert _normalize_name("  EAGLE S  ") == "EAGLE S"


class TestFuzzyMatchWithTransliteration:
    def _make_vessel(self, name, flag=None):
        v = MagicMock()
        v.name = name
        v.flag = flag
        return v

    def test_fuzzy_match_with_transliteration(self):
        """Verify Cyrillic names match their ASCII equivalents via normalization."""
        from app.modules.watchlist_loader import _fuzzy_match_vessel

        db = MagicMock()
        # Vessel in DB has the ASCII transliteration
        vessel = self._make_vessel("BALTIISK")
        db.query.return_value.filter.return_value.all.return_value = [vessel]

        # Search with Cyrillic name
        result = _fuzzy_match_vessel(db, "\u0411\u0410\u041b\u0422\u0418\u0418\u0421\u041a", threshold=85)
        # After transliteration, both sides become ASCII and should match
        assert result is not None
        assert result[0] is vessel
        assert result[2] >= 85

    def test_fuzzy_match_accented_to_ascii(self):
        """Accented names should match plain ASCII equivalents."""
        from app.modules.watchlist_loader import _fuzzy_match_vessel

        db = MagicMock()
        vessel = self._make_vessel("SAO TOME TRADER")
        db.query.return_value.filter.return_value.all.return_value = [vessel]

        result = _fuzzy_match_vessel(db, "S\u00e3o Tom\u00e9 Trader", threshold=85)
        assert result is not None
        assert result[0] is vessel
        assert result[2] >= 92  # High confidence expected for identical after normalization
