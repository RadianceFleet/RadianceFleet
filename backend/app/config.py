from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str = (
        "postgresql+psycopg2://radiancefleet:radiancefleet@localhost:5432/radiancefleet"
    )
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
    AISSTREAM_DEFAULT_DURATION: int = 300
    # Global Fishing Watch API
    GFW_API_TOKEN: str | None = None
    GFW_API_BASE_URL: str = "https://gateway.api.globalfishingwatch.org"
    # Copernicus CDSE — Sentinel-1 SAR catalog
    COPERNICUS_CLIENT_ID: str | None = None
    COPERNICUS_CLIENT_SECRET: str | None = None
    # AISHub — batch AIS positions
    AISHUB_USERNAME: str | None = None


settings = Settings()
