"""Tests for MMSI MID-to-flag mapping and flag risk classification."""
import pytest

from app.utils.vessel_identity import (
    mmsi_to_flag,
    flag_to_risk_category,
    RUSSIAN_ORIGIN_FLAGS,
    MID_TO_FLAG,
)
from app.models.base import FlagRiskEnum


class TestMmsiToFlag:
    """Test MMSI Maritime Identification Digit extraction."""

    @pytest.mark.parametrize("mmsi,expected", [
        ("273123456", "RU"),   # Russia
        ("511234567", "PW"),   # Palau
        ("538000001", "MH"),   # Marshall Islands
        ("226123456", "FR"),   # France
        ("316000000", "CA"),   # Canada
        ("366000001", "US"),   # United States
        ("431999999", "JP"),   # Japan
        ("636000000", "LR"),   # Liberia
        ("351000000", "PA"),   # Panama
        ("215000000", "MT"),   # Malta
        ("620123456", "KM"),   # Comoros
        ("674123456", "TZ"),   # Tanzania
    ])
    def test_known_mids(self, mmsi: str, expected: str):
        assert mmsi_to_flag(mmsi) == expected

    @pytest.mark.parametrize("mmsi", [
        "",           # empty
        "12",         # too short
        "abc",        # non-numeric
        None,         # None
        "00",         # too short
    ])
    def test_invalid_mmsi_returns_none(self, mmsi):
        assert mmsi_to_flag(mmsi) is None

    def test_unknown_mid_returns_none(self):
        # MID 999 is not mapped
        assert mmsi_to_flag("999000000") is None


class TestFlagToRiskCategory:
    """Test flag risk classification."""

    @pytest.mark.parametrize("flag,expected", [
        ("RU", FlagRiskEnum.HIGH_RISK),
        ("CM", FlagRiskEnum.HIGH_RISK),
        ("PW", FlagRiskEnum.HIGH_RISK),
        ("MH", FlagRiskEnum.HIGH_RISK),
        ("KM", FlagRiskEnum.HIGH_RISK),
        ("SL", FlagRiskEnum.HIGH_RISK),
        ("HN", FlagRiskEnum.HIGH_RISK),
        ("GA", FlagRiskEnum.HIGH_RISK),
        ("TZ", FlagRiskEnum.HIGH_RISK),
    ])
    def test_high_risk_flags(self, flag: str, expected: FlagRiskEnum):
        assert flag_to_risk_category(flag) == expected

    @pytest.mark.parametrize("flag,expected", [
        ("US", FlagRiskEnum.LOW_RISK),
        ("NO", FlagRiskEnum.LOW_RISK),
        ("GB", FlagRiskEnum.LOW_RISK),
        ("DK", FlagRiskEnum.LOW_RISK),
        ("JP", FlagRiskEnum.LOW_RISK),
    ])
    def test_low_risk_flags(self, flag: str, expected: FlagRiskEnum):
        assert flag_to_risk_category(flag) == expected

    @pytest.mark.parametrize("flag,expected", [
        ("PA", FlagRiskEnum.MEDIUM_RISK),
        ("LR", FlagRiskEnum.MEDIUM_RISK),
        ("MT", FlagRiskEnum.MEDIUM_RISK),
        ("SG", FlagRiskEnum.MEDIUM_RISK),
        ("GR", FlagRiskEnum.MEDIUM_RISK),
    ])
    def test_medium_risk_flags(self, flag: str, expected: FlagRiskEnum):
        assert flag_to_risk_category(flag) == expected

    def test_none_flag_returns_unknown(self):
        assert flag_to_risk_category(None) == FlagRiskEnum.UNKNOWN

    def test_empty_flag_returns_unknown(self):
        assert flag_to_risk_category("") == FlagRiskEnum.UNKNOWN

    def test_case_insensitive(self):
        assert flag_to_risk_category("ru") == FlagRiskEnum.HIGH_RISK
        assert flag_to_risk_category("us") == FlagRiskEnum.LOW_RISK


class TestRussianOriginFlags:
    """Verify RUSSIAN_ORIGIN_FLAGS constant consistency."""

    def test_all_russian_origin_flags_are_high_risk(self):
        for flag in RUSSIAN_ORIGIN_FLAGS:
            assert flag_to_risk_category(flag) == FlagRiskEnum.HIGH_RISK, (
                f"Flag {flag} in RUSSIAN_ORIGIN_FLAGS should be HIGH_RISK"
            )

    def test_russian_origin_flags_are_frozenset(self):
        assert isinstance(RUSSIAN_ORIGIN_FLAGS, frozenset)

    def test_expected_flags_present(self):
        expected = {"PW", "MH", "KM", "SL", "HN", "GA", "CM", "TZ"}
        assert RUSSIAN_ORIGIN_FLAGS == expected
