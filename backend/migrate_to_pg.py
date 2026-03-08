"""Migrate local SQLite database to remote PostgreSQL using COPY.

Usage:
    DATABASE_URL=postgresql+psycopg2://... uv run python migrate_to_pg.py
"""
import io
import os
import sys

sys.stdout.reconfigure(line_buffering=True)

from sqlalchemy import create_engine, text, inspect as sa_inspect

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "radiancefleet.db")
PG_URL = os.environ.get("DATABASE_URL")

if not PG_URL:
    print("ERROR: Set DATABASE_URL to the PostgreSQL connection string")
    sys.exit(1)

sqlite_engine = create_engine(f"sqlite:///{SQLITE_PATH}")
pg_engine = create_engine(PG_URL)

# All tables to migrate
TABLES = [
    "vessels", "ports", "corridors",
    "ais_points", "ais_observations", "ais_gap_events",
    "fleet_alerts", "spoofing_anomalies", "sts_transfer_events",
    "port_calls", "vessel_owners", "vessel_watchlist",
    "dark_vessel_detections", "merge_candidates", "merge_chains",
    "merge_operations", "evidence_cards", "tip_submissions",
    "hunt_candidates", "ground_truth_vessels", "audit_logs",
    "ingestion_status", "pipeline_runs", "satellite_checks",
    "loitering_events", "draught_change_events", "convoy_events",
    "verification_logs", "vessel_history", "collection_runs",
    "corridor_gap_baselines", "data_coverage_windows",
    "search_missions", "vessel_target_profiles", "vessel_fingerprints",
    "alert_subscriptions", "owner_clusters", "owner_cluster_members",
    "dark_zones", "movement_envelopes", "satellite_tasking_candidates",
    "route_templates", "crea_voyages",
]


def build_copy_buffer(table_name, common_cols, bool_cols):
    """Read from SQLite and build tab-delimited buffer for COPY."""
    col_list = ", ".join(common_cols)
    buf = io.StringIO()
    count = 0

    with sqlite_engine.connect() as src:
        rows = src.execute(text(f"SELECT {col_list} FROM [{table_name}]"))
        for row in rows:
            fields = []
            for i, col_name in enumerate(common_cols):
                val = row[i]
                if val is None:
                    fields.append("\\N")
                elif col_name in bool_cols:
                    fields.append("t" if val else "f")
                else:
                    s = str(val)
                    s = s.replace("\\", "\\\\")
                    s = s.replace("\t", "\\t")
                    s = s.replace("\n", "\\n")
                    s = s.replace("\r", "\\r")
                    fields.append(s)
            buf.write("\t".join(fields) + "\n")
            count += 1

    buf.seek(0)
    return buf, count


def main():
    print("Initializing PostgreSQL schema...")
    os.environ["DATABASE_URL"] = PG_URL
    sys.path.insert(0, os.path.dirname(__file__))

    from app.models import Base
    Base.metadata.create_all(bind=pg_engine)

    from app.database import _run_migrations
    import app.database as db_mod
    original_engine = db_mod.engine
    db_mod.engine = pg_engine
    try:
        _run_migrations()
    finally:
        db_mod.engine = original_engine

    pg_inspector = sa_inspect(pg_engine)
    pg_tables = set(pg_inspector.get_table_names())

    # Pre-compute column info for each table
    table_info = {}
    for table_name in TABLES:
        if table_name not in pg_tables:
            continue
        pg_columns = {c["name"]: c for c in pg_inspector.get_columns(table_name)}
        sqlite_inspector = sa_inspect(sqlite_engine)
        sqlite_columns = {c["name"] for c in sqlite_inspector.get_columns(table_name)}
        common_cols = sorted(sqlite_columns & set(pg_columns.keys()))
        if not common_cols:
            continue
        bool_cols = set()
        for col_name in common_cols:
            pg_type = str(pg_columns[col_name].get("type", "")).upper()
            if "BOOLEAN" in pg_type:
                bool_cols.add(col_name)
        table_info[table_name] = (common_cols, bool_cols)

    # Pre-build all buffers from SQLite (read everything first)
    print("Reading SQLite data...")
    buffers = {}
    for table_name in TABLES:
        if table_name not in table_info:
            continue
        common_cols, bool_cols = table_info[table_name]
        buf, count = build_copy_buffer(table_name, common_cols, bool_cols)
        if count > 0:
            buffers[table_name] = (buf, count, common_cols)
            print(f"  {table_name}: {count} rows read")
        else:
            print(f"  {table_name}: empty")

    # Now do all PG operations on a single raw connection
    print("\nWriting to PostgreSQL...")
    raw_conn = pg_engine.raw_connection()
    cursor = raw_conn.cursor()
    total = 0

    try:
        # Disable FK checks and triggers
        cursor.execute("SET session_replication_role = 'replica'")

        # Truncate all tables in one statement (CASCADE handles FKs)
        all_pg_tables = [t for t in TABLES if t in pg_tables]
        cursor.execute(f"TRUNCATE TABLE {', '.join(all_pg_tables)} CASCADE")
        print("  Truncated all tables")

        # COPY each table
        for table_name in TABLES:
            if table_name not in buffers:
                continue
            buf, count, common_cols = buffers[table_name]
            col_list = ", ".join(common_cols)

            try:
                cursor.copy_expert(f"COPY {table_name} ({col_list}) FROM STDIN", buf)
                total += count
                print(f"  {table_name}: {count} rows")
            except Exception as e:
                raw_conn.rollback()
                print(f"  {table_name}: FAILED — {str(e)[:200]}")
                # Re-disable after rollback
                cursor.execute("SET session_replication_role = 'replica'")

        # Reset sequences for all tables with data
        print("\nResetting sequences...")
        for table_name in buffers:
            pk = pg_inspector.get_pk_constraint(table_name)
            if pk and pk.get("constrained_columns"):
                pk_col = pk["constrained_columns"][0]
                cursor.execute(f"SELECT COALESCE(MAX({pk_col}), 0) FROM {table_name}")
                max_id = cursor.fetchone()[0]
                if max_id and max_id > 0:
                    cursor.execute(
                        f"SELECT pg_get_serial_sequence('{table_name}', '{pk_col}')"
                    )
                    seq = cursor.fetchone()[0]
                    if seq:
                        cursor.execute(f"SELECT setval('{seq}', {max_id})")

        # Re-enable FK checks
        cursor.execute("SET session_replication_role = 'origin'")
        raw_conn.commit()

    except Exception as e:
        raw_conn.rollback()
        print(f"\nFATAL: {e}")
        raise
    finally:
        cursor.close()
        raw_conn.close()

    print(f"\nDone! {total} total rows migrated.")


if __name__ == "__main__":
    main()
