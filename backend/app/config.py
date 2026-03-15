from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8")

    # ── Core ────────────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite:///radiancefleet.db"
    CORRIDORS_CONFIG: str = "config/corridors.yaml"
    RISK_SCORING_CONFIG: str = "config/risk_scoring.yaml"
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"
    DATA_DIR: str = "data"
    PUBLIC_URL: str = "http://localhost:5173"

    # ── Database ────────────────────────────────────────────────────────────
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    MAX_UPLOAD_SIZE_MB: int = 500
    MAX_QUERY_LIMIT: int = 500

    # ── API & Auth ──────────────────────────────────────────────────────────
    RADIANCEFLEET_API_KEY: str | None = None
    CORS_ORIGINS: str = "http://localhost:5173"
    ADMIN_JWT_SECRET: str | None = None  # Generate: openssl rand -hex 32
    ADMIN_PASSWORD: str | None = None  # Strong password for POST /admin/login
    EDIT_LOCK_TTL_SECONDS: int = 300
    RATE_LIMIT_VIEWER: str = "30/minute"
    RATE_LIMIT_ADMIN: str = "120/minute"
    RATE_LIMIT_DEFAULT: str = "60/minute"
    SSE_MAX_CONNECTIONS: int = 20

    # ── Detection Thresholds ────────────────────────────────────────────────
    GAP_MIN_HOURS: float = 2.0
    GAP_ALERT_HOURS: float = 6.0
    STS_PROXIMITY_METERS: float = 200.0
    STS_MIN_WINDOWS: int = 8  # 8 × 15 min = 2 hours sustained
    CLASS_B_NOISE_FILTER_SECONDS: int = 180
    LOITER_GAP_LINKAGE_HOURS: int = 48
    ANCHORAGE_TOLERANCE_DEG: float = 0.05  # ~5.5 km bounding-box tolerance for anchorage corridors
    FUZZY_MATCH_THRESHOLD: int = 85
    COVERAGE_CONFIG: str = "config/coverage.yaml"

    # ── AIS Data Sources ────────────────────────────────────────────────────
    # aisstream.io — real-time WebSocket
    AISSTREAM_API_KEY: str | None = None
    AISSTREAM_WS_URL: str = "wss://stream.aisstream.io/v0/stream"
    AISSTREAM_BATCH_INTERVAL: int = 30
    AISSTREAM_DEFAULT_DURATION: int = 3600
    AISSTREAM_WORKER_ENABLED: bool = False
    # Digitraffic (Finland)
    DIGITRAFFIC_ENABLED: bool = True
    # AISHub
    AISHUB_USERNAME: str | None = None
    AISHUB_ENABLED: bool = False
    # Kystverket (Norway) AIS TCP stream
    KYSTVERKET_ENABLED: bool = True
    KYSTVERKET_HOST: str = "153.44.253.27"
    KYSTVERKET_PORT: int = 5631
    # DMA (Danish Maritime Authority) historical AIS
    DMA_ENABLED: bool = True
    # BarentsWatch (Norwegian EEZ) AIS REST API
    BARENTSWATCH_ENABLED: bool = False
    BARENTSWATCH_CLIENT_ID: str = ""
    BARENTSWATCH_CLIENT_SECRET: str = ""
    BARENTSWATCH_TOKEN_URL: str = "https://id.barentswatch.no/connect/token"  # noqa: S105
    BARENTSWATCH_API_URL: str = "https://live.ais.barentswatch.no/api"
    # NOAA historical AIS
    NOAA_BASE_URL: str = "https://coast.noaa.gov/htdata/CMSP/AISDataHandler"
    # Collection scheduler
    COLLECT_DIGITRAFFIC_INTERVAL: int = 1800  # 30 min
    COLLECT_AISSTREAM_INTERVAL: int = 300  # 5 min
    COLLECT_KYSTVERKET_INTERVAL: int = 300  # 5 min
    COLLECT_BARENTSWATCH_INTERVAL: int = 1800  # 30 min
    COLLECT_DATALASTIC_INTERVAL: int = 3600  # 60 min
    COLLECT_RETENTION_DAYS: int = 90
    DATA_FETCH_TIMEOUT: float = 120.0

    # ── AIS Data Retention ──────────────────────────────────────────────────
    AIS_OBSERVATION_RETENTION_HOURS: int = 72
    RETENTION_DAYS_REALTIME: int = 90
    RETENTION_DAYS_HISTORICAL: int | None = None  # None = keep forever

    # ── External APIs ───────────────────────────────────────────────────────
    # Global Fishing Watch
    GFW_API_TOKEN: str | None = None
    GFW_API_BASE_URL: str = "https://gateway.api.globalfishingwatch.org"
    # Copernicus CDSE — Sentinel-1 SAR catalog
    COPERNICUS_CLIENT_ID: str | None = None
    COPERNICUS_CLIENT_SECRET: str | None = None
    # CREA Russia Fossil Tracker
    CREA_ENABLED: bool = True
    CREA_API_BASE_URL: str = "https://api.russiafossiltracker.com"
    # Equasis (metadata enrichment — ToS requires opt-in)
    EQUASIS_USERNAME: str | None = None
    EQUASIS_PASSWORD: str | None = None
    EQUASIS_SCRAPING_ENABLED: bool = False
    # Paid verification providers
    SKYLIGHT_API_KEY: str = ""
    SPIRE_API_KEY: str = ""
    SEAWEB_API_KEY: str = ""
    VERIFICATION_MONTHLY_BUDGET_USD: float = 500.0

    # ── Vessel Registry APIs ────────────────────────────────────────────
    DATALASTIC_API_KEY: str | None = None

    # ── Satellite Imagery Ordering ───────────────────────────────────────
    PLANET_API_KEY: str | None = None
    CAPELLA_API_KEY: str | None = None
    MAXAR_API_KEY: str | None = None
    MAXAR_USERNAME: str | None = None
    UMBRA_CLIENT_ID: str | None = None
    UMBRA_API_KEY: str | None = None
    SATELLITE_MONTHLY_BUDGET_USD: float = 2000.0
    SATELLITE_ORDER_AUTO_SUBMIT: bool = False

    # ── Vessel Identity Merging ─────────────────────────────────────────────
    MERGE_MAX_SPEED_KN: float = 16.0
    MERGE_MAX_GAP_DAYS: int = 30
    MERGE_AUTO_CONFIDENCE_THRESHOLD: int = 75
    MERGE_CANDIDATE_MIN_CONFIDENCE: int = 50
    HISTORY_CROSS_REFERENCE_ENABLED: bool = True

    # ── Detection Feature Flags ─────────────────────────────────────────────
    # Track naturalness
    TRACK_NATURALNESS_ENABLED: bool = True
    TRACK_NATURALNESS_SCORING_ENABLED: bool = True
    # Draught intelligence
    DRAUGHT_DETECTION_ENABLED: bool = True
    DRAUGHT_SCORING_ENABLED: bool = True
    # Identity fraud
    STATELESS_MMSI_DETECTION_ENABLED: bool = True
    STATELESS_MMSI_SCORING_ENABLED: bool = True
    FLAG_HOPPING_DETECTION_ENABLED: bool = True
    FLAG_HOPPING_SCORING_ENABLED: bool = True
    IMO_FRAUD_DETECTION_ENABLED: bool = True
    IMO_FRAUD_SCORING_ENABLED: bool = True
    # Feed outage detection
    FEED_OUTAGE_DETECTION_ENABLED: bool = True
    # Coverage quality tagging
    COVERAGE_QUALITY_TAGGING_ENABLED: bool = True
    # Dark STS
    DARK_STS_DETECTION_ENABLED: bool = True
    DARK_STS_SCORING_ENABLED: bool = True
    # Fleet analysis
    FLEET_ANALYSIS_ENABLED: bool = True
    FLEET_SCORING_ENABLED: bool = True
    # P&I validation
    PI_VALIDATION_SCORING_ENABLED: bool = True
    # ── P&I Insurance Verification ──────────────────────────────────────────
    PI_VERIFICATION_ENABLED: bool = False
    PI_VERIFICATION_SCORING_ENABLED: bool = True
    # Fraudulent registry
    FRAUDULENT_REGISTRY_SCORING_ENABLED: bool = True
    # Stale AIS data
    STALE_AIS_DETECTION_ENABLED: bool = True
    STALE_AIS_SCORING_ENABLED: bool = True
    # At-sea extended operations
    AT_SEA_OPERATIONS_SCORING_ENABLED: bool = True
    # ISM/P&I continuity
    ISM_CONTINUITY_DETECTION_ENABLED: bool = True
    ISM_CONTINUITY_SCORING_ENABLED: bool = True
    # Rename velocity
    RENAME_VELOCITY_DETECTION_ENABLED: bool = True
    RENAME_VELOCITY_SCORING_ENABLED: bool = True
    # Destination manipulation
    DESTINATION_DETECTION_ENABLED: bool = True
    DESTINATION_SCORING_ENABLED: bool = True
    # STS relay chains
    STS_CHAIN_DETECTION_ENABLED: bool = True
    STS_CHAIN_SCORING_ENABLED: bool = True
    # Scrapped registry + track replay
    SCRAPPED_REGISTRY_DETECTION_ENABLED: bool = True
    SCRAPPED_REGISTRY_SCORING_ENABLED: bool = True
    TRACK_REPLAY_DETECTION_ENABLED: bool = True
    TRACK_REPLAY_SCORING_ENABLED: bool = True
    # MMSI Zombie Detection
    MMSI_ZOMBIE_DETECTION_ENABLED: bool = True
    MMSI_ZOMBIE_SCORING_ENABLED: bool = True
    # MMSI chain detection
    MERGE_CHAIN_DETECTION_ENABLED: bool = True
    MERGE_CHAIN_SCORING_ENABLED: bool = True
    # Behavioral fingerprinting
    FINGERPRINT_ENABLED: bool = True
    # Satellite-AIS correlation
    SAR_CORRELATION_ENABLED: bool = True
    # Corporate ownership graph
    OWNERSHIP_GRAPH_ENABLED: bool = True
    OWNERSHIP_GRAPH_SCORING_ENABLED: bool = True
    # Convoy + floating storage + Arctic corridor
    CONVOY_DETECTION_ENABLED: bool = True
    CONVOY_SCORING_ENABLED: bool = True
    # Voyage prediction + cargo inference + weather
    VOYAGE_PREDICTION_ENABLED: bool = True
    VOYAGE_SCORING_ENABLED: bool = True
    CARGO_INFERENCE_ENABLED: bool = True
    WEATHER_CORRELATION_ENABLED: bool = True
    # Missing evasion technique detectors
    ROUTE_LAUNDERING_DETECTION_ENABLED: bool = True
    ROUTE_LAUNDERING_SCORING_ENABLED: bool = True
    ROUTE_LAUNDERING_LOOKBACK_DAYS: int = 180
    PI_CYCLING_DETECTION_ENABLED: bool = True
    PI_CYCLING_SCORING_ENABLED: bool = True
    SPARSE_TRANSMISSION_DETECTION_ENABLED: bool = True
    SPARSE_TRANSMISSION_SCORING_ENABLED: bool = True
    TYPE_CONSISTENCY_DETECTION_ENABLED: bool = True
    TYPE_CONSISTENCY_SCORING_ENABLED: bool = True
    # AIS reporting anomaly detection
    AIS_REPORTING_ANOMALY_ENABLED: bool = False
    AIS_REPORTING_ANOMALY_SCORING_ENABLED: bool = False
    # Watchlist stub scoring
    WATCHLIST_STUB_SCORING_ENABLED: bool = True

    # ── Gap-SAR Validation ──────────────────────────────────────────────────
    GAP_SAR_VALIDATION_ENABLED: bool = True
    GAP_SAR_SEARCH_RADIUS_NM: float = 30.0
    GAP_SAR_TIME_WINDOW_H: int = 12

    # ── Historical Data Pipeline ────────────────────────────────────────────
    HISTORY_BACKFILL_ENABLED: bool = False
    NOAA_BACKFILL_ENABLED: bool = False
    DMA_BACKFILL_ENABLED: bool = False
    GFW_GAPS_BACKFILL_ENABLED: bool = False
    GFW_ENCOUNTERS_BACKFILL_ENABLED: bool = False
    GFW_PORT_VISITS_BACKFILL_ENABLED: bool = False
    HISTORY_BACKFILL_INTERVAL_HOURS: int = 168  # 1 week

    # ── Email Notifications ─────────────────────────────────────────────────
    RESEND_API_KEY: str | None = None
    EMAIL_FROM_DOMAIN: str = "radiancefleet.com"
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASS: str | None = None

    # ── Sentry Error Tracking ───────────────────────────────────────────────
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1
    SENTRY_ENVIRONMENT: str = "production"

    # ── VIIRS Nighttime Lights ──────────────────────────────────────────────
    VIIRS_ENABLED: bool = False
    VIIRS_EOG_BASE_URL: str = "https://eogdata.mines.edu/wwwdata/viirs_products/vbd/v23"
    VIIRS_DATA_DIR: str = "data/viirs"
    VIIRS_SCORING_ENABLED: bool = True
    COLLECT_VIIRS_INTERVAL: int = 86400  # 24 hours
    VIIRS_GAS_FLARING_FILTER_ENABLED: bool = True
    VIIRS_GAS_FLARING_EXCLUSION_RADIUS_NM: float = 5.0

    # ── Yente Sanctions Screening ───────────────────────────────────────────
    YENTE_API_URL: str = "http://localhost:8100"
    YENTE_API_KEY: str | None = None
    YENTE_ENABLED: bool = False
    YENTE_MATCH_THRESHOLD: float = 0.7
    YENTE_DATASETS: str = "default"

    # ── Equasis Ownership ───────────────────────────────────────────────────
    EQUASIS_OWNERSHIP_RATE_LIMIT_S: float = 10.0
    EQUASIS_OWNERSHIP_MAX_HOPS: int = 5

    # ── Aisstream Worker ────────────────────────────────────────────────────
    AISSTREAM_WORKER_HEALTH_PORT: int = 8001
    AISSTREAM_WORKER_RECONNECT_DELAY_S: int = 5
    AISSTREAM_WORKER_MAX_RECONNECT_ATTEMPTS: int = 100
    AISSTREAM_WORKER_STATS_INTERVAL_S: int = 60

    # ── OFAC SDN Sync ────────────────────────────────────────────────────
    OFAC_SDN_WEBHOOK_ON_NEW: bool = True

    # ── Isolation Forest Anomaly Detection ────────────────────────────────
    ISOLATION_FOREST_ENABLED: bool = False
    ISOLATION_FOREST_SCORING_ENABLED: bool = False

    # ── DBSCAN Trajectory Clustering ─────────────────────────────────────
    DBSCAN_CLUSTERING_ENABLED: bool = False
    DBSCAN_CLUSTERING_SCORING_ENABLED: bool = False
    DBSCAN_EPS_NM: float = 15.0
    DBSCAN_MIN_SAMPLES: int = 3

    # ── GFW SAR Enhancement ──────────────────────────────────────────────
    GFW_SAR_SWEEP_INTERVAL_HOURS: int = 24
    GFW_SAR_MIN_CONFIDENCE: float = 0.5

    # ── OpenCorporates (Beneficial Ownership) ───────────────────────────
    OPENCORPORATES_API_KEY: str = ""
    OPENCORPORATES_ENABLED: bool = False
    OPENCORPORATES_API_URL: str = "https://api.opencorporates.com/v0.4"
    OPENCORPORATES_RATE_LIMIT_S: float = 2.0
    OPENCORPORATES_MONTHLY_QUOTA: int = 500

    # ── Ownership Transparency Scoring ────────────────────────────────
    OWNERSHIP_TRANSPARENCY_SCORING_ENABLED: bool = True

    # ── Analyst Collaboration ──────────────────────────────────────────────
    AUTO_ASSIGNMENT_ENABLED: bool = False
    WORKLOAD_PRIORITY_WEIGHTING_ENABLED: bool = True
    AUTO_ASSIGN_MIN_SCORE: int = 51

    # ── Trajectory Autoencoder Anomaly Detection ────────────────────────────
    TRAJECTORY_AUTOENCODER_ENABLED: bool = False
    TRAJECTORY_AUTOENCODER_SCORING_ENABLED: bool = False
    TRAJECTORY_AUTOENCODER_EPOCHS: int = 200
    TRAJECTORY_AUTOENCODER_LEARNING_RATE: float = 0.1

    # --- v4.2: Trajectory PCA ---
    TRAJECTORY_PCA_ENABLED: bool = True
    TRAJECTORY_PCA_SCORING_ENABLED: bool = True
    TRAJECTORY_PCA_N_COMPONENTS: int = 4

    # --- v4.2: Behavioral Baseline ---
    BEHAVIORAL_BASELINE_ENABLED: bool = True
    BEHAVIORAL_BASELINE_SCORING_ENABLED: bool = True

    # --- v4.2: STS Hotspot Detection ---
    STS_HOTSPOT_ENABLED: bool = True
    STS_HOTSPOT_SCORING_ENABLED: bool = True

    # --- v4.2: GPS Jamming Zone Detection ---
    JAMMING_DETECTION_ENABLED: bool = True
    JAMMING_DETECTION_SPATIAL_EPS_DEG: float = 0.5
    JAMMING_DETECTION_TEMPORAL_EPS_HOURS: float = 2.0
    JAMMING_DETECTION_MIN_VESSELS: int = 3

    # --- v4.2: FP Tuning ---
    FP_TUNING_ENABLED: bool = True
    REGIONAL_FP_TUNING_ENABLED: bool = True

    # ── Auto-Calibration ─────────────────────────────────────────────
    AUTO_CALIBRATION_ENABLED: bool = False
    AUTO_CALIBRATION_MAX_ADJUSTMENT_PCT: int = 15
    AUTO_CALIBRATION_COOLDOWN_DAYS: int = 7

    # --- v4.2: Embed Widget ---
    EMBED_CORS_ORIGINS: str = ""

    # ── Operations ───────────────────────────────────────────────────────
    PROMETHEUS_ENABLED: bool = False

    @model_validator(mode="after")
    def _check_admin_auth_consistency(self) -> "Settings":
        if self.ADMIN_PASSWORD and not self.ADMIN_JWT_SECRET:
            raise ValueError(
                "ADMIN_JWT_SECRET must be set when ADMIN_PASSWORD is configured. "
                "Generate one with: openssl rand -hex 32"
            )
        return self


settings = Settings()
