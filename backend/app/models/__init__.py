"""Import all models to register them with SQLAlchemy metadata."""
from app.models.base import Base
from app.models.vessel import Vessel
from app.models.ais_point import AISPoint
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
from app.models.stubs import VesselTargetProfile, SearchMission, HuntCandidate, DarkVesselDetection

__all__ = [
    "Base",
    "Vessel",
    "AISPoint",
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
    "VesselTargetProfile",
    "SearchMission",
    "HuntCandidate",
    "DarkVesselDetection",
]
