from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator
from app.config import settings

_engine_kwargs: dict = {"pool_pre_ping": True}
if "sqlite" in settings.DATABASE_URL:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
    _engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)

if "sqlite" in settings.DATABASE_URL:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. Called on first run or after migrations."""
    from app.models import Base  # noqa: F401 — ensure all models are registered
    Base.metadata.create_all(bind=engine)
    _run_migrations()


def _run_migrations() -> None:
    """Idempotent ALTER TABLE migrations for columns added after initial schema.

    Uses sqlalchemy.inspect() to check column existence before ALTER — real SQL
    errors (syntax, permissions) propagate instead of being silently swallowed.
    """
    from sqlalchemy import inspect as sa_inspect, text

    inspector = sa_inspect(engine)

    # (table_name, column_name, column_type_sql)
    column_migrations = [
        ("ais_gap_events", "gap_off_lat", "REAL"),
        ("ais_gap_events", "gap_off_lon", "REAL"),
        ("ais_gap_events", "gap_on_lat", "REAL"),
        ("ais_gap_events", "gap_on_lon", "REAL"),
        ("ais_gap_events", "source", "VARCHAR(20)"),
        ("port_calls", "raw_port_name", "VARCHAR"),
        ("port_calls", "source", "VARCHAR NOT NULL DEFAULT 'manual'"),
        # Phase C15 — ownership verification fields
        ("vessel_owners", "verified_by", "VARCHAR"),
        ("vessel_owners", "verified_at", "DATETIME"),
        ("vessel_owners", "source_url", "VARCHAR"),
        ("vessel_owners", "verification_notes", "TEXT"),
        # Phase H1 — data freshness
        ("vessels", "last_ais_received_utc", "DATETIME"),
        # Stage 1 — new detector schema additions
        ("ais_points", "draught", "REAL"),
        ("ais_observations", "draught", "REAL"),
        ("vessel_owners", "ism_manager", "VARCHAR(500)"),
        ("vessel_owners", "pi_club_name", "VARCHAR(200)"),
        ("ports", "is_offshore_terminal", "BOOLEAN DEFAULT 0"),
    ]

    _col_cache: dict[str, set[str]] = {}

    with engine.connect() as conn:
        for table_name, col_name, col_type in column_migrations:
            if table_name not in _col_cache:
                _col_cache[table_name] = {
                    c["name"] for c in inspector.get_columns(table_name)
                }
            if col_name not in _col_cache[table_name]:
                conn.execute(text(
                    f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"
                ))
                conn.commit()
                _col_cache[table_name].add(col_name)

    # Postgres-only: add new enum values to native ENUM type.
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction on Postgres.
    # Use raw DBAPI connection in autocommit mode. IF NOT EXISTS (PG 9.3+)
    # makes this idempotent — real errors propagate.
    if engine.dialect.name == "postgresql":
        raw_conn = engine.raw_connection()
        try:
            raw_conn.set_isolation_level(0)  # ISOLATION_LEVEL_AUTOCOMMIT
            cursor = raw_conn.cursor()
            for val in ("synthetic_track", "stateless_mmsi", "flag_hopping", "imo_fraud"):
                cursor.execute(
                    f"ALTER TYPE spoofingtypeenum ADD VALUE IF NOT EXISTS '{val}'"
                )
            cursor.close()
        finally:
            raw_conn.close()
