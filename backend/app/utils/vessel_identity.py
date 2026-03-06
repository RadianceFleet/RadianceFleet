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
    "236": "GI",  # Gibraltar (UK territory)
    "211": "DE", "218": "DE",
    "226": "FR", "227": "FR", "228": "FR",
    "244": "NL", "245": "NL", "246": "NL",
    "247": "IT",
    "263": "PT", "255": "PT",  # Portugal + Madeira
    "224": "ES", "225": "ES", "221": "ES",
    "205": "BE", "208": "BE",
    "250": "IE",
    "253": "LU",  # Luxembourg
    "261": "PL",  # Poland
    "269": "CH",  # Switzerland (EU associate)
    "238": "HR",  # Croatia
    "204": "PT",  # Portugal (alt block)
    "251": "IS",  # Iceland (EEA)
    "275": "LV", "276": "EE", "277": "LT",  # Baltic EU states
    "264": "RO",  # Romania
    "209": "CY", "210": "CY", "212": "CY",  # Cyprus
    # Americas
    "303": "US", "338": "US", "366": "US", "367": "US", "368": "US", "369": "US",
    "316": "CA",
    "319": "KY",  # Cayman Islands (UK territory)
    "341": "MX",
    "710": "BR",
    # Panama extensions
    "371": "PA", "372": "PA", "373": "PA",
    # Other
    "503": "AU",
    "512": "NZ",
    "601": "ZA",
    "416": "IN", "419": "IN",
    "271": "TR", "272": "TR",
    "622": "EG",
    "470": "AE",
    "403": "SA",
    # Landlocked / anomalous registries (high suspicion)
    "457": "MN",  # Mongolia (landlocked — any seagoing vessel is suspicious)
    # Other open/convenience registries
    "645": "MU",  # Mauritius
    "659": "NA",  # Namibia
    "432": "JP",  # Japan (additional block)
    "477": "HK",  # Hong Kong
    # ── Caribbean / Central America (300-329 block) ──────────────────────────
    "312": "BZ",  # Belize
    "306": "VG",  # British Virgin Islands
    "329": "KN",  # Saint Kitts and Nevis
    "305": "AG",  # Antigua and Barbuda
    "325": "GD",  # Grenada
    "330": "LC",  # Saint Lucia
    "341": "MX",  # Mexico (additional coverage)
    "345": "NI",  # Nicaragua
    "346": "PA",  # Panama (additional block)
    "350": "PA",  # Panama (additional block)
    "359": "JM",  # Jamaica
    "362": "TT",  # Trinidad and Tobago
    "378": "DO",  # Dominican Republic
    "379": "PR",  # Puerto Rico (US territory)
    # ── Middle East (400-470 block) ──────────────────────────────────────────
    "401": "IQ",  # Iraq — Basra/Umm Qasr
    "447": "KW",  # Kuwait
    "466": "QA",  # Qatar — LNG export hub
    "432": "JP",  # Japan (Gulf/Middle East liaison vessels)
    "468": "OM",  # Oman
    "434": "IR",  # Iran (additional block)
    "422": "IR",  # Iran (main block)
    "408": "BH",  # Bahrain
    "455": "JO",  # Jordan
    "450": "IL",  # Israel
    "462": "YE",  # Yemen
    "407": "SY",  # Syria
    "440": "KR",  # Korea (additional Middle East flag ops)
    # ── South / Southeast Asia (500-574 block) ───────────────────────────────
    "405": "BD",  # Bangladesh
    "417": "LK",  # Sri Lanka
    "506": "MM",  # Myanmar
    "533": "MY",  # Malaysia (additional block)
    "502": "MV",  # Maldives
    "508": "NP",  # Nepal (landlocked — suspicious for seagoing)
    "514": "KH",  # Cambodia
    "515": "KH",  # Cambodia (additional block)
    "516": "BN",  # Brunei
    "567": "TH",  # Thailand
    "578": "VN",  # Vietnam (additional block)
    # ── West / Central Africa (600-660 block) ────────────────────────────────
    "663": "SN",  # Senegal
    "619": "CI",  # Côte d'Ivoire (Ivory Coast)
    "627": "GH",  # Ghana
    "657": "NG",  # Nigeria (additional block)
    "609": "BJ",  # Benin
    "610": "BW",  # Botswana (landlocked — suspicious)
    "611": "BF",  # Burkina Faso (landlocked)
    "612": "CF",  # Central African Republic (landlocked)
    "615": "MR",  # Mauritania
    "616": "ML",  # Mali (landlocked)
    "618": "GW",  # Guinea-Bissau
    "621": "DJ",  # Djibouti
    "625": "ER",  # Eritrea
    "633": "GW",  # Guinea-Bissau (additional)
    "641": "ZZ",  # Placeholder for unallocated West Africa
    "648": "SD",  # Sudan
    "649": "SO",  # Somalia
    "654": "SC",  # Seychelles
    "655": "LY",  # Libya
    "660": "ET",  # Ethiopia (landlocked)
    # ── East Africa / Indian Ocean (664-699 block) ───────────────────────────
    "664": "TZ",  # Tanzania (additional block)
    "665": "UG",  # Uganda (landlocked)
    "666": "MZ",  # Mozambique
    "669": "KE",  # Kenya
    "670": "ZA",  # South Africa (additional block)
    "671": "ZM",  # Zambia (landlocked)
    "672": "ZW",  # Zimbabwe (landlocked)
    "677": "DZ",  # Algeria
    "678": "MA",  # Morocco
    "681": "TN",  # Tunisia
    "687": "CV",  # Cape Verde
    # ── Russian internal waterway / far east registries ──────────────────────
    "461": "RU",  # Russian Far Eastern River Fleet
    "462": "RU",  # Russia (additional — military auxiliary)
    "274": "RU",  # Russia (additional Pacific block)
    # ── Oceania / Pacific ────────────────────────────────────────────────────
    "520": "PN",  # Pitcairn Island (UK territory)
    "521": "TO",  # Tonga
    "522": "WS",  # Samoa
    "523": "FJ",  # Fiji
    "529": "TK",  # Tokelau
    "540": "NC",  # New Caledonia (France)
    "546": "PF",  # French Polynesia
    "555": "WF",  # Wallis and Futuna (France)
    "559": "WS",  # Samoa (additional block)
    "570": "SB",  # Solomon Islands
    "576": "VU",  # Vanuatu (additional block)
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


def validate_imo_checksum(imo: str | None) -> bool:
    """Validate IMO number using the check digit algorithm.

    IMO numbers are 7 digits (optionally prefixed with "IMO").
    Check digit = last digit.
    Sum of (digit_i * (7-i)) for i=0..5 mod 10 == check digit.

    Handles None input, "IMO" prefix stripping, and rejects "0000000".
    """
    if not imo:
        return False
    digits = str(imo).replace("IMO", "").replace("imo", "").strip()
    if not digits.isdigit() or len(digits) != 7:
        return False
    if digits == "0000000":
        return False
    total = sum(int(digits[i]) * (7 - i) for i in range(6))
    return total % 10 == int(digits[6])
