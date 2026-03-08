"""Migrate local SQLite database to remote PostgreSQL using COPY.

Usage:
    DATABASE_URL=postgresql+psycopg2://... uv run python migrate_to_pg.py
    DATABASE_URL=postgresql+psycopg2://... uv run python migrate_to_pg.py --force
"""

import argparse
import io
import os
import sys

sys.stdout.reconfigure(line_buffering=True)

from sqlalchemy import create_engine, text
from sqlalchemy import inspect as sa_inspect

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "radiancefleet.db")
PG_URL = os.environ.get("DATABASE_URL")

if not PG_URL:
    print("ERROR: Set DATABASE_URL to the PostgreSQL connection string")
    sys.exit(1)

sqlite_engine = create_engine(f"sqlite:///{SQLITE_PATH}")
pg_engine = create_engine(PG_URL)

# All tables to migrate
TABLES = [
    "vessels",
    "ports",
    "corridors",
    "ais_points",
    "ais_observations",
    "ais_gap_events",
    "fleet_alerts",
    "spoofing_anomalies",
    "sts_transfer_events",
    "port_calls",
    "vessel_owners",
    "vessel_watchlist",
    "dark_vessel_detections",
    "merge_candidates",
    "merge_chains",
    "merge_operations",
    "evidence_cards",
    "tip_submissions",
    "hunt_candidates",
    "ground_truth_vessels",
    "audit_logs",
    "ingestion_status",
    "pipeline_runs",
    "satellite_checks",
    "loitering_events",
    "draught_change_events",
    "convoy_events",
    "verification_logs",
    "vessel_history",
    "collection_runs",
    "corridor_gap_baselines",
    "data_coverage_windows",
    "search_missions",
    "vessel_target_profiles",
    "vessel_fingerprints",
    "alert_subscriptions",
    "owner_clusters",
    "owner_cluster_members",
    "dark_zones",
    "movement_envelopes",
    "satellite_tasking_candidates",
    "route_templates",
    "crea_voyages",
    # v3.3+ tables
    "analysts",
    "alert_edit_locks",
    "satellite_orders",
    "satellite_order_logs",
    "psc_detentions",
    "saved_filters",
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


def validate_row_counts(sqlite_eng, pg_eng, tables):
    """Compare row counts between SQLite and PostgreSQL for each table."""
    print("\nValidating row counts...")
    mismatches = []
    sqlite_inspector = sa_inspect(sqlite_eng)
    sqlite_tables = set(sqlite_inspector.get_table_names())
    pg_inspector = sa_inspect(pg_eng)
    pg_tables = set(pg_inspector.get_table_names())

    for table in tables:
        src_count = 0
        dst_count = 0
        if table in sqlite_tables:
            with sqlite_eng.connect() as conn:
                src_count = conn.execute(text(f"SELECT COUNT(*) FROM [{table}]")).scalar()
        if table in pg_tables:
            with pg_eng.connect() as conn:
                dst_count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        status = "OK" if src_count == dst_count else "MISMATCH"
        if status == "MISMATCH":
            mismatches.append((table, src_count, dst_count))
        print(f"  {table}: SQLite={src_count} PG={dst_count} [{status}]")

    return mismatches


# Key FK relationships to validate after migration
FK_CHECKS = [
    ("fleet_alerts", "vessel_id", "vessels", "id"),
    ("ais_points", "vessel_id", "vessels", "id"),
    ("ais_observations", "vessel_id", "vessels", "id"),
    ("port_calls", "vessel_id", "vessels", "id"),
    ("evidence_cards", "alert_id", "fleet_alerts", "id"),
    ("merge_operations", "chain_id", "merge_chains", "id"),
]


def check_fk_integrity(pg_eng):
    """Check for orphaned FK references in key tables."""
    print("\nChecking FK integrity...")
    pg_inspector = sa_inspect(pg_eng)
    pg_tables = set(pg_inspector.get_table_names())
    issues = []

    for child_table, child_col, parent_table, parent_col in FK_CHECKS:
        if child_table not in pg_tables or parent_table not in pg_tables:
            continue
        with pg_eng.connect() as conn:
            orphan_count = conn.execute(
                text(
                    f"SELECT COUNT(*) FROM {child_table} c "
                    f"LEFT JOIN {parent_table} p ON c.{child_col} = p.{parent_col} "
                    f"WHERE c.{child_col} IS NOT NULL AND p.{parent_col} IS NULL"
                )
            ).scalar()
        status = "OK" if orphan_count == 0 else f"ORPHANS={orphan_count}"
        if orphan_count > 0:
            issues.append((child_table, child_col, parent_table, orphan_count))
        print(f"  {child_table}.{child_col} -> {parent_table}.{parent_col}: [{status}]")

    return issues


def main():
    parser = argparse.ArgumentParser(description="Migrate SQLite to PostgreSQL")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Truncate destination tables before copying (idempotent re-run)",
    )
    args = parser.parse_args()

    print("Initializing PostgreSQL schema...")
    os.environ["DATABASE_URL"] = PG_URL
    sys.path.insert(0, os.path.dirname(__file__))

    from app.models import Base

    Base.metadata.create_all(bind=pg_engine)

    import app.database as db_mod
    from app.database import _run_migrations

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

        # Check if destination has existing data
        all_pg_tables = [t for t in TABLES if t in pg_tables]
        has_data = False
        for t in all_pg_tables:
            cursor.execute(f"SELECT EXISTS (SELECT 1 FROM {t} LIMIT 1)")
            if cursor.fetchone()[0]:
                has_data = True
                break

        if has_data and not args.force:
            print("  WARNING: Destination tables contain data.")
            print("  Use --force to truncate and re-migrate.")
            cursor.close()
            raw_conn.close()
            sys.exit(1)

        # Truncate all tables in one statement (CASCADE handles FKs)
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
                    cursor.execute(f"SELECT pg_get_serial_sequence('{table_name}', '{pk_col}')")
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

    # --- Post-migration validation ---
    mismatches = validate_row_counts(sqlite_engine, pg_engine, TABLES)
    fk_issues = check_fk_integrity(pg_engine)

    # --- Summary report ---
    print("\n" + "=" * 60)
    print("MIGRATION SUMMARY")
    print("=" * 60)
    print(f"  Total rows migrated: {total}")
    print(f"  Tables processed:    {len(buffers)}")
    print(f"  Row count mismatches: {len(mismatches)}")
    if mismatches:
        for table, src, dst in mismatches:
            print(f"    - {table}: SQLite={src} PG={dst}")
    print(f"  FK integrity issues: {len(fk_issues)}")
    if fk_issues:
        for child, col, parent, count in fk_issues:
            print(f"    - {child}.{col} -> {parent}: {count} orphans")
    if not mismatches and not fk_issues:
        print("  Status: ALL CHECKS PASSED")
    else:
        print("  Status: COMPLETED WITH WARNINGS")
    print("=" * 60)


if __name__ == "__main__":
    main()
