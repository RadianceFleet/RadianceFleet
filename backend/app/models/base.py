"""Shared declarative base and enums for all models."""
from __future__ import annotations

import enum
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class AISClassEnum(str, enum.Enum):
    A = "A"
    B = "B"
    UNKNOWN = "unknown"


class FlagRiskEnum(str, enum.Enum):
    HIGH_RISK = "high_risk"
    MEDIUM_RISK = "medium_risk"
    LOW_RISK = "low_risk"
    UNKNOWN = "unknown"


class PIStatusEnum(str, enum.Enum):
    ACTIVE = "active"
    LAPSED = "lapsed"
    UNKNOWN = "unknown"


class CorridorTypeEnum(str, enum.Enum):
    EXPORT_ROUTE = "export_route"
    STS_ZONE = "sts_zone"
    IMPORT_ROUTE = "import_route"
    ANCHORAGE_HOLDING = "anchorage_holding"
    DARK_ZONE = "dark_zone"
    LEGITIMATE_TRADE_ROUTE = "legitimate_trade_route"
    # Analyst-assigned type for verified clean trade routes (reduces score by 0.7×).
    # NOTE: anchorage_holding is NOT mapped to 0.7× — some anchorages are STS waiting
    # areas (e.g. Laconian Gulf) and need the 2.0× STS multiplier. Only corridors
    # explicitly tagged LEGITIMATE_TRADE_ROUTE by an analyst get the reduction.


class AlertStatusEnum(str, enum.Enum):
    NEW = "new"
    UNDER_REVIEW = "under_review"
    NEEDS_SATELLITE_CHECK = "needs_satellite_check"
    DOCUMENTED = "documented"
    DISMISSED = "dismissed"


class SpoofingTypeEnum(str, enum.Enum):
    ANCHOR_SPOOF = "anchor_spoof"
    CIRCLE_SPOOF = "circle_spoof"
    SLOW_ROLL = "slow_roll"
    MMSI_REUSE = "mmsi_reuse"
    NAV_STATUS_MISMATCH = "nav_status_mismatch"
    ERRATIC_NAV_STATUS = "erratic_nav_status"
    DUAL_TRANSMISSION = "dual_transmission"
    CROSS_RECEIVER_DISAGREEMENT = "cross_receiver_disagreement"
    IDENTITY_SWAP = "identity_swap"
    FAKE_PORT_CALL = "fake_port_call"
    # Sub-types (extended_restricted_maneuverability, nav_status_15) are stored
    # in evidence_json["subtype"] on an ERRATIC_NAV_STATUS anomaly record.


class STSDetectionTypeEnum(str, enum.Enum):
    VISIBLE_VISIBLE = "visible_visible"
    VISIBLE_DARK = "visible_dark"
    DARK_DARK = "dark_dark"
    APPROACHING = "approaching"
    GFW_ENCOUNTER = "gfw_encounter"


class EstimatedMethodEnum(str, enum.Enum):
    LINEAR = "linear"
    SPLINE = "spline"
    KALMAN = "kalman"


class SatelliteReviewStatusEnum(str, enum.Enum):
    NOT_CHECKED = "not_checked"
    CANDIDATE_SCENES_FOUND = "candidate_scenes_found"
    REVIEWED = "reviewed"


class DarkZoneTypeEnum(str, enum.Enum):
    ACTIVE_JAMMING = "active_jamming"
    HISTORICAL_GAP_CLUSTER = "historical_gap_cluster"
    STS_HOTSPOT = "sts_hotspot"


class MergeCandidateStatusEnum(str, enum.Enum):
    PENDING = "pending"
    AUTO_MERGED = "auto_merged"
    ANALYST_MERGED = "analyst_merged"
    REJECTED = "rejected"
