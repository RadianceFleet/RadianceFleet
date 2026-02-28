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

    Each statement is wrapped in try/except so re-running is safe (column already exists).
    """
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    migrations = [
        "ALTER TABLE ais_gap_events ADD COLUMN gap_off_lat REAL",
        "ALTER TABLE ais_gap_events ADD COLUMN gap_off_lon REAL",
        "ALTER TABLE ais_gap_events ADD COLUMN gap_on_lat REAL",
        "ALTER TABLE ais_gap_events ADD COLUMN gap_on_lon REAL",
        "ALTER TABLE ais_gap_events ADD COLUMN source VARCHAR(20)",
        "ALTER TABLE vessels ADD COLUMN last_ais_received_utc DATETIME",
    ]
    with engine.connect() as conn:
        for stmt in migrations:
            try:
                conn.execute(text(stmt))
                conn.commit()
            except OperationalError:
                conn.rollback()  # Column already exists — safe to ignore
