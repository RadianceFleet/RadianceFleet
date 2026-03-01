from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8"
    )

    DATABASE_URL: str = "sqlite:///radiancefleet.db"
    CORRIDORS_CONFIG: str = "config/corridors.yaml"
    RISK_SCORING_CONFIG: str = "config/risk_scoring.yaml"
    LOG_LEVEL: str = "INFO"
    # Gap detection thresholds (hours)
    GAP_MIN_HOURS: float = 2.0
    GAP_ALERT_HOURS: float = 6.0
    # STS proximity (meters)
    STS_PROXIMITY_METERS: float = 200.0
    STS_MIN_WINDOWS: int = 8  # 8 × 15 min = 2 hours sustained
    # Class B noise filter (seconds) — gaps shorter than this are artifacts
    CLASS_B_NOISE_FILTER_SECONDS: int = 180
    # Loiter-gap linkage window (hours)
    LOITER_GAP_LINKAGE_HOURS: int = 48
    # Watchlist fuzzy match threshold (0-100)
    FUZZY_MATCH_THRESHOLD: int = 85
    # Regional AIS coverage config
    COVERAGE_CONFIG: str = "config/coverage.yaml"
    # Upload and query limits
    MAX_UPLOAD_SIZE_MB: int = 500
    MAX_QUERY_LIMIT: int = 500
    # Connection pool
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    # API authentication (if unset, all requests pass — backward compatible for local dev)
    RADIANCEFLEET_API_KEY: str | None = None
    # CORS origins (comma-separated string for env var support)
    CORS_ORIGINS: str = "http://localhost:5173"
    # Data fetcher settings
    DATA_DIR: str = "data"
    DATA_FETCH_TIMEOUT: float = 120.0
    # aisstream.io — real-time AIS WebSocket
    AISSTREAM_API_KEY: str | None = None
    AISSTREAM_WS_URL: str = "wss://stream.aisstream.io/v0/stream"
    AISSTREAM_BATCH_INTERVAL: int = 30
    AISSTREAM_DEFAULT_DURATION: int = 3600
    # Global Fishing Watch API
    GFW_API_TOKEN: str | None = None
    GFW_API_BASE_URL: str = "https://gateway.api.globalfishingwatch.org"
    # Copernicus CDSE — Sentinel-1 SAR catalog
    COPERNICUS_CLIENT_ID: str | None = None
    COPERNICUS_CLIENT_SECRET: str | None = None
    # AISHub — batch AIS positions
    AISHUB_USERNAME: str | None = None
    # NOAA historical AIS data
    NOAA_BASE_URL: str = "https://coast.noaa.gov/htdata/CMSP/AISDataHandler"
    # Kystverket (Norway) AIS TCP stream
    KYSTVERKET_ENABLED: bool = False
    KYSTVERKET_HOST: str = "153.44.253.27"
    KYSTVERKET_PORT: int = 5631
    # Digitraffic (Finland) Marine API
    DIGITRAFFIC_ENABLED: bool = False
    # CREA Russia Fossil Tracker
    CREA_ENABLED: bool = False
    CREA_API_BASE_URL: str = "https://api.russiafossiltracker.com"
    # Vessel identity merging
    MERGE_MAX_SPEED_KN: float = 16.0
    MERGE_MAX_GAP_DAYS: int = 30
    MERGE_AUTO_CONFIDENCE_THRESHOLD: int = 85
    MERGE_CANDIDATE_MIN_CONFIDENCE: int = 50
    # Paid verification providers (Phase D17-19)
    SKYLIGHT_API_KEY: str = ""
    SPIRE_API_KEY: str = ""
    SEAWEB_API_KEY: str = ""
    VERIFICATION_MONTHLY_BUDGET_USD: float = 500.0
    # Phase K: Track naturalness
    TRACK_NATURALNESS_ENABLED: bool = False
    TRACK_NATURALNESS_SCORING_ENABLED: bool = False
    # Phase L: Draught intelligence
    DRAUGHT_DETECTION_ENABLED: bool = False
    DRAUGHT_SCORING_ENABLED: bool = False
    # Phase M: Identity fraud
    STATELESS_MMSI_DETECTION_ENABLED: bool = False
    STATELESS_MMSI_SCORING_ENABLED: bool = False
    FLAG_HOPPING_DETECTION_ENABLED: bool = False
    FLAG_HOPPING_SCORING_ENABLED: bool = False
    IMO_FRAUD_DETECTION_ENABLED: bool = False
    IMO_FRAUD_SCORING_ENABLED: bool = False
    # Stage 1-A: Feed outage detection
    FEED_OUTAGE_DETECTION_ENABLED: bool = False
    # Stage 1-C: Coverage quality tagging (metadata only, never reduces score)
    COVERAGE_QUALITY_TAGGING_ENABLED: bool = False
    # Phase N: Dark STS
    DARK_STS_DETECTION_ENABLED: bool = False
    DARK_STS_SCORING_ENABLED: bool = False
    # Phase O: Fleet analysis
    FLEET_ANALYSIS_ENABLED: bool = False
    FLEET_SCORING_ENABLED: bool = False
    # Stage 2-A: P&I validation
    PI_VALIDATION_DETECTION_ENABLED: bool = False
    PI_VALIDATION_SCORING_ENABLED: bool = False
    # Stage 2-B: Fraudulent registry
    FRAUDULENT_REGISTRY_DETECTION_ENABLED: bool = False
    FRAUDULENT_REGISTRY_SCORING_ENABLED: bool = False
    # Stage 2-C: Stale AIS data detection
    STALE_AIS_DETECTION_ENABLED: bool = False
    STALE_AIS_SCORING_ENABLED: bool = False
    # Stage 2-D: At-sea extended operations
    AT_SEA_OPERATIONS_SCORING_ENABLED: bool = False
    # Stage 2-E: ISM/P&I continuity
    ISM_CONTINUITY_DETECTION_ENABLED: bool = False
    ISM_CONTINUITY_SCORING_ENABLED: bool = False
    # Stage 2-F: Rename velocity
    RENAME_VELOCITY_DETECTION_ENABLED: bool = False
    RENAME_VELOCITY_SCORING_ENABLED: bool = False
    # Stage 3-A: Destination manipulation
    DESTINATION_DETECTION_ENABLED: bool = False
    DESTINATION_SCORING_ENABLED: bool = False
    # Stage 3-B: STS relay chains
    STS_CHAIN_DETECTION_ENABLED: bool = False
    STS_CHAIN_SCORING_ENABLED: bool = False
    # Stage 3-C: Scrapped registry + track replay
    SCRAPPED_REGISTRY_DETECTION_ENABLED: bool = False
    SCRAPPED_REGISTRY_SCORING_ENABLED: bool = False
    TRACK_REPLAY_DETECTION_ENABLED: bool = False
    TRACK_REPLAY_SCORING_ENABLED: bool = False
    # Stage 4-A: Extended MMSI chain detection
    MERGE_CHAIN_DETECTION_ENABLED: bool = False
    MERGE_CHAIN_SCORING_ENABLED: bool = False
    # Stage 4-B: Behavioral fingerprinting
    FINGERPRINT_ENABLED: bool = False
    FINGERPRINT_SCORING_ENABLED: bool = False
    # Stage 4-C: Satellite-AIS correlation
    SAR_CORRELATION_ENABLED: bool = False
    SAR_CORRELATION_SCORING_ENABLED: bool = False


settings = Settings()
