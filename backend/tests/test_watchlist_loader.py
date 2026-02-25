"""Watchlist loader tests — MMSI validation and fuzzy matching."""
from unittest.mock import MagicMock

import pytest

from app.modules.watchlist_loader import _is_valid_mmsi, _fuzzy_match_vessel


class TestIsValidMMSI:
    def test_valid_9_digit(self):
        assert _is_valid_mmsi("123456789") is True

    def test_valid_with_whitespace(self):
        assert _is_valid_mmsi("  123456789  ") is True

    def test_invalid_8_digits(self):
        assert _is_valid_mmsi("12345678") is False

    def test_invalid_10_digits(self):
        assert _is_valid_mmsi("1234567890") is False

    def test_invalid_letters(self):
        assert _is_valid_mmsi("12345678a") is False

    def test_empty_string(self):
        assert _is_valid_mmsi("") is False

    def test_none_value(self):
        assert _is_valid_mmsi(None) is False


class TestFuzzyMatchVessel:
    def _make_vessel(self, name, flag=None):
        v = MagicMock()
        v.name = name
        v.flag = flag
        return v

    def test_exact_match_returns_vessel(self):
        db = MagicMock()
        vessel = self._make_vessel("TANKER ONE")
        db.query.return_value.filter.return_value.all.return_value = [vessel]
        result = _fuzzy_match_vessel(db, "TANKER ONE")
        assert result is vessel

    def test_below_threshold_returns_none(self):
        db = MagicMock()
        vessel = self._make_vessel("COMPLETELY DIFFERENT NAME")
        db.query.return_value.filter.return_value.all.return_value = [vessel]
        result = _fuzzy_match_vessel(db, "TANKER ONE", threshold=85)
        assert result is None

    def test_above_threshold_matches(self):
        db = MagicMock()
        vessel = self._make_vessel("TANKER ONEE")  # minor typo
        db.query.return_value.filter.return_value.all.return_value = [vessel]
        result = _fuzzy_match_vessel(db, "TANKER ONE", threshold=85)
        assert result is vessel

    def test_empty_name_returns_none(self):
        db = MagicMock()
        result = _fuzzy_match_vessel(db, "")
        assert result is None

    def test_none_name_returns_none(self):
        db = MagicMock()
        result = _fuzzy_match_vessel(db, None)
        assert result is None

    def test_no_candidates_returns_none(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        result = _fuzzy_match_vessel(db, "TANKER ONE")
        assert result is None

    def test_candidate_with_none_name_skipped(self):
        db = MagicMock()
        v_none = self._make_vessel(None)
        v_real = self._make_vessel("TANKER ONE")
        db.query.return_value.filter.return_value.all.return_value = [v_none, v_real]
        result = _fuzzy_match_vessel(db, "TANKER ONE")
        assert result is v_real
