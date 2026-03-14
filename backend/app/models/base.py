"""Shared declarative base and enums for all models."""

from __future__ import annotations

import enum

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class AISClassEnum(enum.StrEnum):
    A = "A"
    B = "B"
    UNKNOWN = "unknown"


class FlagRiskEnum(enum.StrEnum):
    HIGH_RISK = "high_risk"
    MEDIUM_RISK = "medium_risk"
    LOW_RISK = "low_risk"
    UNKNOWN = "unknown"


class PIStatusEnum(enum.StrEnum):
    ACTIVE = "active"
    LAPSED = "lapsed"
    UNKNOWN = "unknown"


class CorridorTypeEnum(enum.StrEnum):
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


class AlertStatusEnum(enum.StrEnum):
    NEW = "new"
    UNDER_REVIEW = "under_review"
    NEEDS_SATELLITE_CHECK = "needs_satellite_check"
    DOCUMENTED = "documented"
    DISMISSED = "dismissed"
    CONFIRMED_FP = "confirmed_fp"
    CONFIRMED_TP = "confirmed_tp"


class SpoofingTypeEnum(enum.StrEnum):
    ANCHOR_SPOOF = "anchor_spoof"
    CIRCLE_SPOOF = "circle_spoof"
    CIRCLE_SPOOF_STATIONARY = "circle_spoof_stationary"
    CIRCLE_SPOOF_DELIBERATE = "circle_spoof_deliberate"
    CIRCLE_SPOOF_EQUIPMENT = "circle_spoof_equipment"
    SLOW_ROLL = "slow_roll"
    MMSI_REUSE = "mmsi_reuse"
    NAV_STATUS_MISMATCH = "nav_status_mismatch"
    ERRATIC_NAV_STATUS = "erratic_nav_status"
    DUAL_TRANSMISSION = "dual_transmission"
    CROSS_RECEIVER_DISAGREEMENT = "cross_receiver_disagreement"
    IDENTITY_SWAP = "identity_swap"
    FAKE_PORT_CALL = "fake_port_call"
    # Phase K-M detection types
    SYNTHETIC_TRACK = "synthetic_track"
    STATELESS_MMSI = "stateless_mmsi"
    FLAG_HOPPING = "flag_hopping"
    IMO_FRAUD = "imo_fraud"
    # Stage 2-C: Repeating AIS data values (stale transponder data)
    STALE_AIS_DATA = "stale_ais_data"
    # Stage 3-A: Destination manipulation
    DESTINATION_DEVIATION = "destination_deviation"
    # Stage 3-C: Historical track replay
    TRACK_REPLAY = "track_replay"
    # Stage C: Missing evasion technique detectors
    ROUTE_LAUNDERING = "route_laundering"
    PI_CYCLING = "pi_cycling"
    SPARSE_TRANSMISSION = "sparse_transmission"
    TYPE_DWT_MISMATCH = "type_dwt_mismatch"
    # Sub-types (extended_restricted_maneuverability, nav_status_15) are stored
    # in evidence_json["subtype"] on an ERRATIC_NAV_STATUS anomaly record.


class STSDetectionTypeEnum(enum.StrEnum):
    VISIBLE_VISIBLE = "visible_visible"
    VISIBLE_DARK = "visible_dark"
    DARK_DARK = "dark_dark"
    APPROACHING = "approaching"
    GFW_ENCOUNTER = "gfw_encounter"


class EstimatedMethodEnum(enum.StrEnum):
    LINEAR = "linear"
    SPLINE = "spline"
    KALMAN = "kalman"


class SatelliteReviewStatusEnum(enum.StrEnum):
    NOT_CHECKED = "not_checked"
    CANDIDATE_SCENES_FOUND = "candidate_scenes_found"
    REVIEWED = "reviewed"


class DarkZoneTypeEnum(enum.StrEnum):
    ACTIVE_JAMMING = "active_jamming"
    HISTORICAL_GAP_CLUSTER = "historical_gap_cluster"
    STS_HOTSPOT = "sts_hotspot"


class AnalystRoleEnum(enum.StrEnum):
    ANALYST = "analyst"
    SENIOR_ANALYST = "senior_analyst"
    ADMIN = "admin"


class MergeCandidateStatusEnum(enum.StrEnum):
    PENDING = "pending"
    AUTO_MERGED = "auto_merged"
    ANALYST_MERGED = "analyst_merged"
    REJECTED = "rejected"
