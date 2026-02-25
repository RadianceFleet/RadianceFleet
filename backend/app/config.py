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


settings = Settings()
