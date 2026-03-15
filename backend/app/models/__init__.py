"""Import all models to register them with SQLAlchemy metadata."""

from app.models.ais_observation import AISObservation
from app.models.ais_point import AISPoint
from app.models.alert_edit_lock import AlertEditLock
from app.models.alert_subscription import AlertSubscription
from app.models.analyst import Analyst
from app.models.api_key import ApiKey
from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.calibration_event import CalibrationEvent
from app.models.case_activity import CaseActivity
from app.models.case_analyst import CaseAnalyst
from app.models.case_alert import CaseAlert
from app.models.collection_run import CollectionRun
from app.models.convoy_event import ConvoyEvent
from app.models.corridor import Corridor
from app.models.corridor_gap_baseline import CorridorGapBaseline
from app.models.corridor_scoring_override import CorridorScoringOverride
from app.models.crea_voyage import CreaVoyage
from app.models.dark_zone import DarkZone
from app.models.data_coverage_window import DataCoverageWindow
from app.models.draught_event import DraughtChangeEvent
from app.models.evidence_card import EvidenceCard
from app.models.flag_risk_profile import FlagRiskProfile
from app.models.fleet_alert import FleetAlert
from app.models.fp_rate_snapshot import FPRateSnapshot
from app.models.gap_event import AISGapEvent
from app.models.ground_truth import GroundTruthVessel
from app.models.handoff_note import HandoffNote
from app.models.ingestion_status import IngestionStatus
from app.models.investigation_case import InvestigationCase
from app.models.isolation_forest_anomaly import IsolationForestAnomaly
from app.models.jamming_zone import JammingZone, JammingZoneGap
from app.models.loitering_event import LoiteringEvent
from app.models.merge_candidate import MergeCandidate
from app.models.notification_event import NotificationEvent
from app.models.merge_chain import MergeChain
from app.models.merge_operation import MergeOperation
from app.models.movement_envelope import MovementEnvelope
from app.models.owner_cluster import OwnerCluster
from app.models.owner_cluster_member import OwnerClusterMember
from app.models.pipeline_run import PipelineRun
from app.models.port import Port
from app.models.port_call import PortCall
from app.models.psc_detention import PscDetention
from app.models.route_template import RouteTemplate
from app.models.satellite_check import SatelliteCheck
from app.models.satellite_order import SatelliteOrder
from app.models.satellite_order_log import SatelliteOrderLog
from app.models.satellite_tasking_candidate import SatelliteTaskingCandidate
from app.models.saved_filter import SavedFilter
from app.models.scoring_region import ScoringRegion
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.sts_hotspot import StsHotspot
from app.models.sts_transfer import StsTransferEvent
from app.models.stubs import DarkVesselDetection, HuntCandidate, SearchMission, VesselTargetProfile
from app.models.tip_submission import TipSubmission
from app.models.trajectory_cluster import TrajectoryCluster
from app.models.trajectory_autoencoder_anomaly import TrajectoryAutoencoderAnomaly
from app.models.trajectory_cluster_member import TrajectoryClusterMember
from app.models.trajectory_pca_anomaly import TrajectoryPcaAnomaly
from app.models.verification_log import VerificationLog
from app.models.vessel import Vessel
from app.models.vessel_behavioral_profile import VesselBehavioralProfile
from app.models.vessel_fingerprint import VesselFingerprint
from app.models.vessel_history import VesselHistory
from app.models.vessel_owner import VesselOwner
from app.models.vessel_watchlist import VesselWatchlist
from app.models.webhook import Webhook
from app.models.worker_heartbeat import WorkerHeartbeat

__all__ = [
    "Base",
    "Vessel",
    "AISPoint",
    "AISObservation",
    "DarkZone",
    "Corridor",
    "AISGapEvent",
    "SatelliteCheck",
    "EvidenceCard",
    "VesselHistory",
    "VesselWatchlist",
    "MovementEnvelope",
    "StsTransferEvent",
    "SpoofingAnomaly",
    "LoiteringEvent",
    "Port",
    "PortCall",
    "VesselOwner",
    "MergeCandidate",
    "MergeOperation",
    "VerificationLog",
    "CreaVoyage",
    "VesselTargetProfile",
    "SearchMission",
    "HuntCandidate",
    "DarkVesselDetection",
    "DraughtChangeEvent",
    "CorridorGapBaseline",
    "SatelliteTaskingCandidate",
    "OwnerCluster",
    "OwnerClusterMember",
    "FlagRiskProfile",
    "FleetAlert",
    "FPRateSnapshot",
    "PipelineRun",
    "MergeChain",
    "VesselFingerprint",
    "ConvoyEvent",
    "RouteTemplate",
    "CollectionRun",
    "DataCoverageWindow",
    "TipSubmission",
    "AlertSubscription",
    "IngestionStatus",
    "AuditLog",
    "GroundTruthVessel",
    "Analyst",
    "AlertEditLock",
    "SatelliteOrder",
    "SatelliteOrderLog",
    "PscDetention",
    "SavedFilter",
    "ApiKey",
    "Webhook",
    "TrajectoryCluster",
    "TrajectoryClusterMember",
    "WorkerHeartbeat",
    "IsolationForestAnomaly",
    "TrajectoryPcaAnomaly",
    "VesselBehavioralProfile",
    "StsHotspot",
    "JammingZone",
    "JammingZoneGap",
    "HandoffNote",
    "CorridorScoringOverride",
    "InvestigationCase",
    "CaseActivity",
    "CaseAnalyst",
    "CaseAlert",
    "CalibrationEvent",
    "ScoringRegion",
    "NotificationEvent",
    "TrajectoryAutoencoderAnomaly",
]
