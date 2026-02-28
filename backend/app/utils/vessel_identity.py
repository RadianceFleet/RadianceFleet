"""MMSI MID-to-flag mapping and flag risk classification.

Every AIS message contains an MMSI whose first 3 digits (Maritime Identification
Digits) encode the vessel's flag state per ITU-R M.585. This module provides
the mapping from MID → ISO 3166-1 alpha-2 flag code, and classifies flags into
risk categories for scoring.
"""
from __future__ import annotations

from app.models.base import FlagRiskEnum
from app.utils.itu_mid_table import (
    ITU_MID_ALLOCATION,
    UNALLOCATED_MIDS,
    LANDLOCKED_MIDS,
    MICRO_TERRITORY_MIDS,
)

# Convenience flags known to be used by the Russian shadow fleet
# (source: KSE Institute, CREA, s&p Global shadow fleet tracker).
RUSSIAN_ORIGIN_FLAGS: frozenset[str] = frozenset({
    "PW",  # Palau
    "KM",  # Comoros
    "SL",  # Sierra Leone
    "HN",  # Honduras
    "GA",  # Gabon
    "CM",  # Cameroon
    "TZ",  # Tanzania
    "ST",  # São Tomé and Príncipe — 385% registration increase 2025
    "GM",  # Gambia — cited in KSE shadow fleet reports
    "CK",  # Cook Islands — expelled from RISC 2025
    "GQ",  # Equatorial Guinea — cited in CSIS shadow fleet reports
    "TV",  # Tuvalu — documented shadow fleet re-flagging destination
    "VU",  # Vanuatu — documented shadow fleet re-flagging destination
    "BB",  # Barbados — used by 5-8% of shadow fleet (KSE 2025)
    "GN",  # Guinea — emerging shadow fleet re-flagging destination
    # NOTE: Marshall Islands (MH) intentionally excluded — 2nd largest open registry
    # (~4,000 vessels). MH is MEDIUM_RISK to avoid mass false positives.
})

# Western / major registries with strong oversight
LOW_RISK_FLAGS: frozenset[str] = frozenset({
    "NO", "DK", "SE", "FI", "GB", "DE", "FR", "NL", "IT", "ES", "PT",
    "US", "CA", "JP", "KR", "AU", "NZ", "BE", "IE",
})

# ITU MID → ISO 3166-1 alpha-2 flag code (key maritime MIDs)
MID_TO_FLAG: dict[str, str] = {
    # Russia
    "273": "RU",
    # Shadow fleet convenience flags
    "511": "PW", "538": "MH", "620": "KM", "667": "SL",
    "334": "HN", "626": "GA", "613": "CM", "674": "TZ",
    "668": "ST", "629": "GM", "518": "CK", "631": "GQ",
    "553": "TV", "550": "VU", "314": "BB", "632": "GN",
    # Major open registries
    "351": "PA", "352": "PA", "353": "PA", "354": "PA", "355": "PA", "356": "PA", "357": "PA",
    "636": "LR", "637": "LR",
    "308": "BS", "309": "BS", "311": "BS",
    "215": "MT", "229": "MT", "248": "MT", "249": "MT", "256": "MT",
    "563": "SG", "564": "SG", "565": "SG", "566": "SG",
    "239": "GR", "240": "GR", "241": "GR",
    "370": "PA",  # alternate block
    "572": "TW",
    "431": "JP", "432": "JP",
    "440": "KR", "441": "KR",
    "412": "CN", "413": "CN", "414": "CN",
    "477": "HK",
    "525": "ID",
    "533": "MY",
    "548": "PH",
    "574": "VN",
    # Western Europe / Nordics
    "257": "NO", "258": "NO", "259": "NO",
    "219": "DK", "220": "DK",
    "265": "SE", "266": "SE",
    "230": "FI",
    "232": "GB", "233": "GB", "234": "GB", "235": "GB",
    "211": "DE",
    "226": "FR", "227": "FR", "228": "FR",
    "244": "NL", "245": "NL", "246": "NL",
    "247": "IT",
    "263": "PT",
    "224": "ES", "225": "ES",
    "205": "BE",
    "250": "IE",
    # Americas
    "303": "US", "338": "US", "366": "US", "367": "US", "368": "US", "369": "US",
    "316": "CA",
    # Other
    "503": "AU",
    "512": "NZ",
    "341": "MX",
    "710": "BR",
    "601": "ZA",
    "416": "IN", "419": "IN",
    "271": "TR", "272": "TR",
    "622": "EG",
    "470": "AE",
    "403": "SA",
}


def mmsi_to_flag(mmsi: str) -> str | None:
    """Extract flag state ISO code from MMSI Maritime Identification Digits.

    Args:
        mmsi: 9-digit MMSI string.

    Returns:
        ISO 3166-1 alpha-2 flag code, or None if MMSI is invalid/unknown.
    """
    if not mmsi or not mmsi.isdigit() or len(mmsi) < 3:
        return None
    mid = mmsi[:3]
    return MID_TO_FLAG.get(mid)


def is_suspicious_mid(mmsi: str) -> bool:
    """Check if an MMSI uses an unallocated, landlocked, or micro-territory MID.

    Uses the complete ITU MID allocation table for authoritative checks.
    Returns True for:
    - MIDs in UNALLOCATED_MIDS (no ITU assignment)
    - MIDs in LANDLOCKED_MIDS (suspicious for ocean-going vessels)
    - MIDs in MICRO_TERRITORY_MIDS (uncommon, corroborating signal)
    - MIDs not in ITU_MID_ALLOCATION at all
    """
    if not mmsi or not mmsi.isdigit() or len(mmsi) < 3:
        return False
    mid = int(mmsi[:3])
    if mid in UNALLOCATED_MIDS:
        return True
    if mid in LANDLOCKED_MIDS:
        return True
    if mid in MICRO_TERRITORY_MIDS:
        return True
    if mid not in ITU_MID_ALLOCATION:
        return True
    return False


def flag_to_risk_category(flag: str | None) -> FlagRiskEnum:
    """Classify a flag ISO code into a risk category.

    - HIGH_RISK: Known shadow fleet convenience flags (RUSSIAN_ORIGIN_FLAGS)
      plus Russia itself.
    - LOW_RISK: Major Western registries with strong oversight.
    - MEDIUM_RISK: Everything else (open registries like PA, LR, BS, MT, SG, GR).
    """
    if not flag:
        return FlagRiskEnum.UNKNOWN
    upper = flag.upper()
    if upper in RUSSIAN_ORIGIN_FLAGS or upper == "RU":
        return FlagRiskEnum.HIGH_RISK
    if upper in LOW_RISK_FLAGS:
        return FlagRiskEnum.LOW_RISK
    return FlagRiskEnum.MEDIUM_RISK
