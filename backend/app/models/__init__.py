"""Import all models to register them with SQLAlchemy metadata."""
from app.models.base import Base
from app.models.vessel import Vessel
from app.models.ais_point import AISPoint
from app.models.ais_observation import AISObservation
from app.models.dark_zone import DarkZone
from app.models.corridor import Corridor
from app.models.gap_event import AISGapEvent
from app.models.satellite_check import SatelliteCheck
from app.models.evidence_card import EvidenceCard
from app.models.vessel_history import VesselHistory
from app.models.vessel_watchlist import VesselWatchlist
from app.models.movement_envelope import MovementEnvelope
from app.models.sts_transfer import StsTransferEvent
from app.models.spoofing_anomaly import SpoofingAnomaly
from app.models.loitering_event import LoiteringEvent
from app.models.port import Port
from app.models.port_call import PortCall
from app.models.vessel_owner import VesselOwner
from app.models.merge_candidate import MergeCandidate
from app.models.merge_operation import MergeOperation
from app.models.verification_log import VerificationLog
from app.models.crea_voyage import CreaVoyage
from app.models.draught_event import DraughtChangeEvent
from app.models.corridor_gap_baseline import CorridorGapBaseline
from app.models.satellite_tasking_candidate import SatelliteTaskingCandidate
from app.models.owner_cluster import OwnerCluster
from app.models.owner_cluster_member import OwnerClusterMember
from app.models.fleet_alert import FleetAlert
from app.models.pipeline_run import PipelineRun
from app.models.merge_chain import MergeChain
from app.models.vessel_fingerprint import VesselFingerprint
from app.models.convoy_event import ConvoyEvent
from app.models.route_template import RouteTemplate
from app.models.stubs import VesselTargetProfile, SearchMission, HuntCandidate, DarkVesselDetection

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
    "FleetAlert",
    "PipelineRun",
    "MergeChain",
    "VesselFingerprint",
    "ConvoyEvent",
    "RouteTemplate",
]
