from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# Normalize DATABASE_URL for SQLAlchemy driver compatibility
_db_url = settings.DATABASE_URL
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif _db_url.startswith("postgresql://") and "+psycopg" not in _db_url:
    _db_url = _db_url.replace("postgresql://", "postgresql+psycopg2://", 1)

_engine_kwargs: dict = {"pool_pre_ping": True}
if "sqlite" in _db_url:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
    _engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
engine = create_engine(_db_url, **_engine_kwargs)

if "sqlite" in _db_url:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
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
    _seed_admin_user(SessionLocal)


def _seed_admin_user(session_factory) -> None:
    """Create default admin analyst if table is empty and ADMIN_PASSWORD is set."""
    if not settings.ADMIN_PASSWORD:
        return
    from app.auth import hash_password
    from app.models.analyst import Analyst

    db = session_factory()
    try:
        if db.query(Analyst).count() == 0:
            admin = Analyst(
                username="admin",
                display_name="Administrator",
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                role="admin",
                is_active=True,
            )
            db.add(admin)
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _run_migrations() -> None:
    """Idempotent ALTER TABLE migrations for columns added after initial schema.

    Uses sqlalchemy.inspect() to check column existence before ALTER — real SQL
    errors (syntax, permissions) propagate instead of being silently swallowed.
    """
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

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
        # Identity merge column (required for vessel identity resolution)
        ("vessels", "merged_into_vessel_id", "INTEGER REFERENCES vessels(vessel_id)"),
        # Phase H1 — data freshness
        ("vessels", "last_ais_received_utc", "DATETIME"),
        # Stage 1 — new detector schema additions
        ("ais_points", "draught", "REAL"),
        ("ais_observations", "draught", "REAL"),
        ("vessel_owners", "ism_manager", "VARCHAR(500)"),
        ("vessel_owners", "pi_club_name", "VARCHAR(200)"),
        ("ports", "is_offshore_terminal", "BOOLEAN DEFAULT 0"),
        # Stage 0 — merge bug fixes
        ("spoofing_anomalies", "created_at", "DATETIME"),
        ("ais_gap_events", "original_vessel_id", "INTEGER"),
        # Stage 1 — accuracy foundation
        ("ais_gap_events", "is_feed_outage", "BOOLEAN DEFAULT 0"),
        ("ais_gap_events", "coverage_quality", "VARCHAR(20)"),
        ("vessels", "dark_fleet_confidence", "VARCHAR(20)"),
        ("vessels", "confidence_evidence_json", "TEXT"),
        # Stage 5-A — ownership graph
        ("vessel_owners", "parent_owner_id", "INTEGER"),
        ("vessel_owners", "ownership_type", "VARCHAR(50)"),
        ("vessel_owners", "ownership_pct", "REAL"),
        # Stage B — destination field on AIS points
        ("ais_points", "destination", "VARCHAR(20)"),
        # Historical pipeline — wall-clock time when a point was loaded
        ("ais_points", "ingested_at", "DATETIME"),
        # Track B1 — heuristic DWT flag (derived from GT, not authoritative)
        ("vessels", "is_heuristic_dwt", "BOOLEAN NOT NULL DEFAULT 0"),
        # Track B3 — SeaWeb field write-back: full JSON payload on verification log
        ("verification_logs", "result_json", "TEXT"),
        # Watchlist stub scoring — vessels with no AIS history
        ("vessels", "watchlist_stub_score", "INTEGER"),
        ("vessels", "watchlist_stub_breakdown", "JSON"),
        # OSINT improvements — sanctioned terminal flag on ports
        ("ports", "is_sanctioned", "BOOLEAN DEFAULT 0"),
        # 5C — AIS source timestamp priority
        ("ais_points", "source_timestamp_utc", "DATETIME"),
        # 5B — AIS cargo type from ship_type field
        ("vessels", "ais_cargo_type", "VARCHAR(50)"),
        # STS analyst validation
        ("sts_transfer_events", "user_validated", "BOOLEAN"),
        ("sts_transfer_events", "confidence_override", "REAL"),
        # Analyst verdict fields
        ("ais_gap_events", "is_false_positive", "BOOLEAN"),
        ("ais_gap_events", "reviewed_by", "VARCHAR(100)"),
        ("ais_gap_events", "review_date", "DATETIME"),
        # v3.3 — multi-analyst workflow
        ("audit_logs", "analyst_id", "INTEGER"),
        ("ais_gap_events", "assigned_to", "INTEGER"),
        ("ais_gap_events", "assigned_at", "DATETIME"),
        ("ais_gap_events", "version", "INTEGER NOT NULL DEFAULT 1"),
        ("evidence_cards", "exported_by", "INTEGER"),
        ("evidence_cards", "approved_by", "INTEGER"),
        ("evidence_cards", "approved_at", "DATETIME"),
        ("evidence_cards", "approval_status", "VARCHAR(20)"),
        ("evidence_cards", "approval_notes", "TEXT"),
    ]

    _col_cache: dict[str, set[str]] = {}

    # Map SQLite types to PostgreSQL equivalents
    _is_pg = engine.dialect.name == "postgresql"
    _type_map: dict[str, str] = {}
    if _is_pg:
        _type_map = {
            "DATETIME": "TIMESTAMP",
            "BOOLEAN DEFAULT 0": "BOOLEAN DEFAULT FALSE",
            "BOOLEAN NOT NULL DEFAULT 0": "BOOLEAN NOT NULL DEFAULT FALSE",
            "JSON": "JSONB",
        }

    with engine.connect() as conn:
        for table_name, col_name, col_type in column_migrations:
            if table_name not in _col_cache:
                _col_cache[table_name] = {c["name"] for c in inspector.get_columns(table_name)}
            if col_name not in _col_cache[table_name]:
                mapped_type = _type_map.get(col_type, col_type) if _is_pg else col_type
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {mapped_type}"))
                conn.commit()
                _col_cache[table_name].add(col_name)

    # Idempotent index creation (Stage B — destination lookup)
    _idx_migrations = [
        ("ix_ais_points_destination", "ais_points", "destination"),
    ]
    with engine.connect() as conn:
        existing_indexes: set[str] = set()
        for idx_info in inspector.get_indexes("ais_points"):
            existing_indexes.add(idx_info["name"])
        for idx_name, tbl, col in _idx_migrations:
            if idx_name not in existing_indexes:
                conn.execute(text(f"CREATE INDEX {idx_name} ON {tbl} ({col})"))
                conn.commit()

    # AIS point dedup: remove duplicates and create unique constraint
    existing_uq = {c["name"] for c in inspector.get_unique_constraints("ais_points")}
    if "uq_ais_point_vessel_ts_source" not in existing_uq:
        with engine.connect() as conn:
            # Delete duplicate rows, keeping the highest-quality source per group
            # (prefer non-null source_timestamp_utc, then highest ais_point_id)
            if engine.dialect.name == "sqlite":
                conn.execute(
                    text(
                        "DELETE FROM ais_points WHERE ais_point_id NOT IN ("
                        "  SELECT ais_point_id FROM ("
                        "    SELECT ais_point_id, ROW_NUMBER() OVER ("
                        "      PARTITION BY vessel_id, timestamp_utc, source"
                        "      ORDER BY source_timestamp_utc IS NULL, source_timestamp_utc DESC, ais_point_id DESC"
                        "    ) AS rn FROM ais_points"
                        "  ) WHERE rn = 1"
                        ")"
                    )
                )
            else:
                conn.execute(
                    text(
                        "DELETE FROM ais_points WHERE ais_point_id NOT IN ("
                        "  SELECT ais_point_id FROM ("
                        "    SELECT ais_point_id, ROW_NUMBER() OVER ("
                        "      PARTITION BY vessel_id, timestamp_utc, source"
                        "      ORDER BY source_timestamp_utc DESC NULLS LAST, ais_point_id DESC"
                        "    ) AS rn FROM ais_points"
                        "  ) sub WHERE rn = 1"
                        ")"
                    )
                )
            conn.commit()
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_ais_point_vessel_ts_source "
                    "ON ais_points (vessel_id, timestamp_utc, source)"
                )
            )
            conn.commit()

    # Postgres-only: add new enum values to native ENUM type.
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction on Postgres.
    # Use raw DBAPI connection in autocommit mode. IF NOT EXISTS (PG 9.3+)
    # makes this idempotent — real errors propagate.
    if engine.dialect.name == "postgresql":
        raw_conn = engine.raw_connection()
        try:
            raw_conn.set_isolation_level(0)  # ISOLATION_LEVEL_AUTOCOMMIT
            cursor = raw_conn.cursor()
            for val in (
                "synthetic_track",
                "stateless_mmsi",
                "flag_hopping",
                "imo_fraud",
                "stale_ais_data",
                "destination_deviation",
                "track_replay",
                "route_laundering",
                "pi_cycling",
                "sparse_transmission",
                "type_dwt_mismatch",
            ):
                cursor.execute(f"ALTER TYPE spoofingtypeenum ADD VALUE IF NOT EXISTS '{val}'")
            for val in ("confirmed_fp", "confirmed_tp"):
                cursor.execute(f"ALTER TYPE alertstatusenum ADD VALUE IF NOT EXISTS '{val}'")
            # Analyst role enum
            cursor.execute(
                "DO $$ BEGIN CREATE TYPE analyst_role AS ENUM ('analyst', 'senior_analyst', 'admin'); EXCEPTION WHEN duplicate_object THEN NULL; END $$"
            )
            cursor.close()
        finally:
            raw_conn.close()
