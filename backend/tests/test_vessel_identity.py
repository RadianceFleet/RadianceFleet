"""Tests for MMSI MID-to-flag mapping and flag risk classification."""

import pytest

from app.models.base import FlagRiskEnum
from app.utils.vessel_identity import (
    MID_TO_FLAG,
    RUSSIAN_ORIGIN_FLAGS,
    flag_to_risk_category,
    is_suspicious_mid,
    mmsi_to_flag,
    validate_imo_checksum,
)


class TestMmsiToFlag:
    """Test MMSI Maritime Identification Digit extraction."""

    @pytest.mark.parametrize(
        "mmsi,expected",
        [
            ("273123456", "RU"),  # Russia
            ("511234567", "PW"),  # Palau
            ("538000001", "MH"),  # Marshall Islands
            ("226123456", "FR"),  # France
            ("316000000", "CA"),  # Canada
            ("366000001", "US"),  # United States
            ("431999999", "JP"),  # Japan
            ("636000000", "LR"),  # Liberia
            ("351000000", "PA"),  # Panama
            ("215000000", "MT"),  # Malta
            ("620123456", "KM"),  # Comoros
            ("674123456", "TZ"),  # Tanzania
        ],
    )
    def test_known_mids(self, mmsi: str, expected: str):
        assert mmsi_to_flag(mmsi) == expected

    @pytest.mark.parametrize(
        "mmsi",
        [
            "",  # empty
            "12",  # too short
            "abc",  # non-numeric
            None,  # None
            "00",  # too short
        ],
    )
    def test_invalid_mmsi_returns_none(self, mmsi):
        assert mmsi_to_flag(mmsi) is None

    def test_unknown_mid_returns_none(self):
        # MID 999 is not mapped
        assert mmsi_to_flag("999000000") is None


class TestFlagToRiskCategory:
    """Test flag risk classification."""

    @pytest.mark.parametrize(
        "flag,expected",
        [
            ("RU", FlagRiskEnum.HIGH_RISK),
            ("CM", FlagRiskEnum.HIGH_RISK),
            ("PW", FlagRiskEnum.HIGH_RISK),
            ("KM", FlagRiskEnum.HIGH_RISK),
            ("SL", FlagRiskEnum.HIGH_RISK),
            ("HN", FlagRiskEnum.HIGH_RISK),
            ("GA", FlagRiskEnum.HIGH_RISK),
            ("TZ", FlagRiskEnum.HIGH_RISK),
            ("TV", FlagRiskEnum.HIGH_RISK),
            ("VU", FlagRiskEnum.HIGH_RISK),
        ],
    )
    def test_high_risk_flags(self, flag: str, expected: FlagRiskEnum):
        assert flag_to_risk_category(flag) == expected

    def test_marshall_islands_is_medium_risk(self):
        """MH is MEDIUM_RISK (2nd largest open registry — mass false positives if HIGH)."""
        assert flag_to_risk_category("MH") == FlagRiskEnum.MEDIUM_RISK

    @pytest.mark.parametrize(
        "flag,expected",
        [
            ("US", FlagRiskEnum.LOW_RISK),
            ("NO", FlagRiskEnum.LOW_RISK),
            ("GB", FlagRiskEnum.LOW_RISK),
            ("DK", FlagRiskEnum.LOW_RISK),
            ("JP", FlagRiskEnum.LOW_RISK),
        ],
    )
    def test_low_risk_flags(self, flag: str, expected: FlagRiskEnum):
        assert flag_to_risk_category(flag) == expected

    @pytest.mark.parametrize(
        "flag,expected",
        [
            ("PA", FlagRiskEnum.MEDIUM_RISK),
            ("LR", FlagRiskEnum.MEDIUM_RISK),
            ("MT", FlagRiskEnum.MEDIUM_RISK),
            ("SG", FlagRiskEnum.MEDIUM_RISK),
            ("GR", FlagRiskEnum.MEDIUM_RISK),
        ],
    )
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
        expected = {
            "PW",
            "KM",
            "SL",
            "HN",
            "GA",
            "CM",
            "TZ",
            "ST",
            "GM",
            "CK",
            "GQ",
            "TV",
            "VU",
            "BB",
            "GN",
        }
        assert RUSSIAN_ORIGIN_FLAGS == expected

    @pytest.mark.parametrize("flag", ["ST", "GM", "CK", "GQ"])
    def test_new_shadow_fleet_flags_are_high_risk(self, flag: str):
        """Newly added shadow fleet haven flags classify as HIGH_RISK."""
        assert flag_to_risk_category(flag) == FlagRiskEnum.HIGH_RISK

    @pytest.mark.parametrize("flag", ["ST", "GM", "CK", "GQ"])
    def test_new_flags_have_mids(self, flag: str):
        """Each new flag should have at least one MID mapping."""
        found = any(v == flag for v in MID_TO_FLAG.values())
        assert found, f"Flag {flag} has no MID mapping"


class TestSuspiciousMid:
    """Test unallocated/stateless MID detection."""

    def test_unallocated_mid_is_suspicious(self):
        """MMSI with MID not in MID_TO_FLAG → suspicious."""
        # MID 999 is not allocated to any country
        assert is_suspicious_mid("999000000") is True

    def test_known_stateless_mid_646(self):
        """MID 646 (documented stateless shadow fleet pattern) → suspicious."""
        assert is_suspicious_mid("646123456") is True

    def test_allocated_mid_not_suspicious(self):
        """MMSI with known MID → not suspicious."""
        assert is_suspicious_mid("273123456") is False  # Russia
        assert is_suspicious_mid("351000000") is False  # Panama

    def test_invalid_mmsi_not_suspicious(self):
        """Invalid MMSI → not suspicious (returns False, not error)."""
        assert is_suspicious_mid("") is False
        assert is_suspicious_mid("12") is False
        assert is_suspicious_mid(None) is False


class TestValidateImoChecksum:
    """Test canonical IMO checksum validation."""

    def test_valid_imo(self):
        """IMO 9074729 is a known valid IMO (check digit 9)."""
        assert validate_imo_checksum("9074729") is True

    def test_invalid_checksum(self):
        """IMO with wrong check digit fails."""
        assert validate_imo_checksum("9074720") is False

    def test_imo_prefix_stripped(self):
        """'IMO' prefix is stripped before validation."""
        assert validate_imo_checksum("IMO9074729") is True
        assert validate_imo_checksum("imo9074729") is True
        assert validate_imo_checksum("IMO 9074729") is True

    def test_none_input(self):
        """None input returns False."""
        assert validate_imo_checksum(None) is False

    def test_empty_string(self):
        """Empty string returns False."""
        assert validate_imo_checksum("") is False

    def test_all_zeros_rejected(self):
        """'0000000' is rejected even though checksum technically passes."""
        assert validate_imo_checksum("0000000") is False

    def test_non_digit(self):
        """Non-digit strings fail."""
        assert validate_imo_checksum("ABCDEFG") is False

    def test_wrong_length(self):
        """Strings that are not 7 digits (after prefix strip) fail."""
        assert validate_imo_checksum("123456") is False
        assert validate_imo_checksum("12345678") is False
