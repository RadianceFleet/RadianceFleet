"""CLI commands: check-vessels, open, status, search, rescore, score-stubs,
evaluate-detector, confirm-detector, watchlist-update."""

from __future__ import annotations

import logging

import typer

logger = logging.getLogger(__name__)
from pathlib import Path

import app.cli_helpers as _h
from app.cli_app import app, console


@app.command("check-vessels")
def check_vessels(
    auto: bool = typer.Option(False, "--auto", help="Only show auto-merge results"),
    list_mode: bool = typer.Option(
        False, "--list", help="List pending candidates without interactive review"
    ),
    diagnose: bool = typer.Option(
        False, "--diagnose", help="Show merge readiness diagnostic and exit"
    ),
    min_score: int = typer.Option(
        65, "--min-score", help="Minimum confidence score to show (used with --list)"
    ),
    cleanup: bool = typer.Option(
        False, "--cleanup", help="Remove stale candidates (merged/deleted vessels)"
    ),
):
    """Review and fix vessel identity issues."""
    from app.database import SessionLocal
    from app.models.base import MergeCandidateStatusEnum
    from app.models.merge_candidate import MergeCandidate
    from app.models.vessel import Vessel
    from app.modules.identity_resolver import detect_merge_candidates, execute_merge

    db = SessionLocal()
    try:
        # --cleanup: reject candidates where either vessel has been merged or deleted
        if cleanup:
            stale = 0
            candidates = (
                db.query(MergeCandidate)
                .filter(
                    MergeCandidate.status == MergeCandidateStatusEnum.PENDING,
                )
                .all()
            )
            for c in candidates:
                vessel_a = db.query(Vessel).filter(Vessel.vessel_id == c.vessel_a_id).first()
                vessel_b = db.query(Vessel).filter(Vessel.vessel_id == c.vessel_b_id).first()
                if (
                    not vessel_a
                    or not vessel_b
                    or vessel_a.merged_into_vessel_id is not None
                    or vessel_b.merged_into_vessel_id is not None
                ):
                    c.status = MergeCandidateStatusEnum.REJECTED
                    stale += 1
            if stale:
                db.commit()
            console.print(f"Cleaned up {stale} stale merge candidates")
            return

        # --diagnose: print diagnostic and exit immediately
        if diagnose:
            from app.modules.identity_resolver import diagnose_merge_readiness

            diag = diagnose_merge_readiness(db)
            max_gap_days = diag["merge_config"]["max_gap_days"]
            console.print("[bold]Merge Readiness Diagnostic[/bold]")
            console.print(f"  Total vessels: {diag['total_vessels']}")
            console.print(f"  Vessels with gap events: {diag['vessels_with_gaps']}")
            console.print(f"  Dark candidates (went dark >2h ago): {diag['dark_candidates']}")
            console.print(
                f"  New candidates (appeared last {max_gap_days}d): {diag['new_candidates']}"
            )
            console.print(f"  Avg AIS points/vessel: {diag['avg_points_per_vessel']}")
            if diag["issues"]:
                console.print("\n[bold]Issues:[/bold]")
                for issue in diag["issues"]:
                    console.print(f"  - {issue}")
            return

        # Step 1: Run detection
        with console.status("[bold]Scanning for vessel identity changes..."):
            result = detect_merge_candidates(db)

        console.print(
            f"Auto-merged: {result['auto_merged']} pairs  |  "
            f"Needs review: {result['candidates_created']} pairs"
        )

        if auto:
            return

        # Load pending candidates (apply min_score filter in list mode)
        q = db.query(MergeCandidate).filter(
            MergeCandidate.status == MergeCandidateStatusEnum.PENDING
        )
        if list_mode:
            q = q.filter(MergeCandidate.confidence_score >= min_score)
        candidates = q.order_by(MergeCandidate.confidence_score.desc()).all()

        if not candidates:
            console.print("[green]No vessel identity issues need review.[/green]")
            return

        # List mode or non-TTY fallback
        if list_mode or not _h._is_interactive():
            if not _h._is_interactive() and not list_mode:
                console.print(
                    "[dim]Interactive mode requires a terminal. Use --auto or --list instead.[/dim]"
                )
            _h._print_candidates_table(console, db, candidates)
            return

        # Interactive review
        console.print(f"\n[bold]Reviewing {len(candidates)} candidates:[/bold]\n")
        from datetime import datetime

        for c in candidates:
            va = db.query(Vessel).get(c.vessel_a_id)
            vb = db.query(Vessel).get(c.vessel_b_id)

            console.print(
                f"  Vessel A: {va.mmsi if va else '?'} ({va.name or '?' if va else '?'}, {va.flag or '?' if va else '?'})"
            )
            if c.vessel_a_last_time:
                console.print(f"    Last seen: {str(c.vessel_a_last_time)[:10]}")
            console.print(
                f"  Vessel B: {vb.mmsi if vb else '?'} ({vb.name or '?' if vb else '?'}, {vb.flag or '?' if vb else '?'})"
            )
            if c.vessel_b_first_time:
                console.print(f"    First seen: {str(c.vessel_b_first_time)[:10]}")
            if c.time_delta_hours is not None:
                console.print(
                    f"  Gap: {c.time_delta_hours:.1f} hours, {c.distance_nm:.1f}nm apart"
                    if c.distance_nm
                    else f"  Gap: {c.time_delta_hours:.1f} hours"
                )
            console.print(f"  Confidence: {c.confidence_score}/100\n")

            choice = typer.prompt("  [m]erge  [s]kip  [r]eject  [q]uit", default="s")
            choice = choice.strip().lower()

            if choice == "q":
                console.print("[dim]Exiting review.[/dim]")
                break
            elif choice == "m":
                merge_result = execute_merge(
                    db,
                    c.vessel_a_id,
                    c.vessel_b_id,
                    candidate_id=c.candidate_id,
                    merged_by="analyst_cli",
                )
                if merge_result.get("success"):
                    c.status = MergeCandidateStatusEnum.ANALYST_MERGED
                    c.resolved_at = datetime.utcnow()
                    c.resolved_by = "analyst_cli"
                    db.commit()
                    console.print("  [green]Merged.[/green]\n")
                else:
                    console.print(
                        f"  [yellow]Could not merge: {merge_result.get('error', 'unknown')}[/yellow]\n"
                    )
            elif choice == "r":
                c.status = MergeCandidateStatusEnum.REJECTED
                c.resolved_at = datetime.utcnow()
                c.resolved_by = "analyst_cli"
                db.commit()
                console.print("  [dim]Rejected.[/dim]\n")
            else:
                console.print("  [dim]Skipped.[/dim]\n")

    finally:
        db.close()


@app.command("open")
def open_dashboard(
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser automatically"),
    host: str = typer.Option("127.0.0.1", "--host", hidden=True),
    port: int = typer.Option(8000, "--port", hidden=True),
):
    """Launch the web dashboard."""
    import threading
    import time
    import webbrowser

    import uvicorn

    url = f"http://{host}:{port}"

    if not no_browser:

        def _open_browser():
            time.sleep(1.5)
            webbrowser.open(url)

        threading.Thread(target=_open_browser, daemon=True).start()

    console.print(f"Dashboard running at [cyan]{url}[/cyan] — press Ctrl+C to stop")
    uvicorn.run("app.main:app", host=host, port=port)


@app.command("status")
def status():
    """Show system health and data freshness."""
    from sqlalchemy import func

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        # System checks
        console.print("[bold]System[/bold]")

        from app.models.corridor import Corridor
        from app.models.port import Port

        corr_count = db.query(Corridor).count()
        port_count = db.query(Port).count()

        console.print("  Database: [green]OK[/green]")
        console.print(
            f"  Corridors: {'[green]' + str(corr_count) + ' loaded[/green]' if corr_count else '[yellow]not loaded[/yellow]'}"
        )
        console.print(
            f"  Ports: {'[green]' + str(port_count) + ' seeded[/green]' if port_count else '[yellow]not seeded[/yellow]'}"
        )

        # Data freshness
        console.print("\n[bold]Data Freshness[/bold]")

        from app.models.ais_point import AISPoint

        ais_count = db.query(AISPoint).count()
        ais_latest = db.query(func.max(AISPoint.timestamp_utc)).scalar()

        if ais_latest:
            from datetime import datetime

            age = datetime.utcnow() - ais_latest
            age_hours = age.total_seconds() / 3600
            age_str = f"{age_hours:.0f} hours ago" if age_hours < 48 else f"{age.days} days ago"
            freshness_color = "green" if age_hours < 24 else "yellow" if age_hours < 72 else "red"
            console.print(
                f"  AIS data: [{freshness_color}]Last import {age_str}[/{freshness_color}] ({ais_count:,} positions)"
            )
        else:
            console.print("  AIS data: [red]No data yet[/red]")

        from app.models.vessel_watchlist import VesselWatchlist

        wl_count = db.query(VesselWatchlist).filter(VesselWatchlist.is_active).count()
        wl_latest = db.query(func.max(VesselWatchlist.date_listed)).scalar()
        if wl_count > 0:
            if wl_latest:
                console.print(
                    f"  Watchlists: [green]{wl_count} active entries[/green] (latest listing: {wl_latest})"
                )
            else:
                console.print(f"  Watchlists: [green]{wl_count} active entries[/green]")
        else:
            console.print("  Watchlists: [dim]Never imported[/dim]")

        # Results
        console.print("\n[bold]Results[/bold]")

        from app.models.gap_event import AISGapEvent
        from app.models.vessel import Vessel

        vessel_count = db.query(Vessel).filter(Vessel.merged_into_vessel_id.is_(None)).count()
        alert_count = db.query(AISGapEvent).count()
        scored_count = db.query(AISGapEvent).filter(AISGapEvent.risk_score.isnot(None)).count()

        console.print(f"  Vessels tracked: {vessel_count:,}")
        console.print(f"  Alerts: {alert_count:,} ({scored_count:,} scored)")

        if scored_count > 0:
            critical = db.query(AISGapEvent).filter(AISGapEvent.risk_score >= 76).count()
            high = (
                db.query(AISGapEvent)
                .filter(AISGapEvent.risk_score >= 51, AISGapEvent.risk_score < 76)
                .count()
            )
            medium = (
                db.query(AISGapEvent)
                .filter(AISGapEvent.risk_score >= 26, AISGapEvent.risk_score < 51)
                .count()
            )
            console.print(
                f"    [red]{critical} critical[/red]  "
                f"[yellow]{high} high[/yellow]  "
                f"[dim]{medium} medium[/dim]"
            )

        # Suggestion
        if ais_latest:
            from datetime import datetime

            age = datetime.utcnow() - ais_latest
            if age.total_seconds() > 86400:
                console.print(
                    f"\n[yellow]Your data is {age.days} day{'s' if age.days != 1 else ''} old. "
                    f"Run [cyan]radiancefleet update[/cyan] to refresh.[/yellow]"
                )
        elif corr_count == 0:
            console.print(
                "\n[yellow]Not set up yet. Run [cyan]radiancefleet start[/cyan] to begin.[/yellow]"
            )

    finally:
        db.close()


@app.command("rescore")
def rescore(
    incremental: bool = typer.Option(False, "--incremental", help="Only rescore dirty vessels"),
):
    """Re-run scoring without re-running detectors."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        if incremental:
            from app.modules.incremental_scorer import incremental_score_alerts

            with console.status("[bold]Incremental re-scoring..."):
                result = incremental_score_alerts(db)
            console.print(
                f"[green]Incremental rescore: scored={result.get('scored', 0)} "
                f"skipped={result.get('skipped', 0)} "
                f"config_changed={result.get('config_changed', False)}[/green] "
                f"(config hash: {result.get('config_hash', '?')})"
            )
            return

        from app.modules.risk_scoring import rescore_all_alerts

        with console.status("[bold]Re-scoring all alerts..."):
            result = rescore_all_alerts(db)
        console.print(
            f"[green]Rescored {result.get('rescored', 0)} alerts[/green] "
            f"(config hash: {result.get('config_hash', '?')})"
        )

        # Run confidence classification after rescore
        try:
            from app.modules.confidence_classifier import classify_all_vessels

            with console.status("[bold]Classifying vessel confidence..."):
                cls_result = classify_all_vessels(db)
            by_level = cls_result.get("by_level", {})
            console.print(
                f"  Classified {cls_result.get('classified', 0)} vessels: "
                + ", ".join(f"{k}={v}" for k, v in sorted(by_level.items()))
            )
        except ImportError:
            logger.error("Could not import confidence_classifier module")

        # Score watchlist stubs (vessels with no AIS history)
        try:
            from app.modules.risk_scoring import score_watchlist_stubs

            with console.status("[bold]Scoring watchlist stubs..."):
                stub_result = score_watchlist_stubs(db)
            console.print(
                f"  Stub scoring: scored={stub_result.get('scored', 0)} "
                f"cleared={stub_result.get('cleared', 0)}"
            )
        except (ImportError, AttributeError) as e:
            logger.error("Could not run watchlist stub scoring: %s", e)
    finally:
        db.close()


@app.command("score-stubs")
def score_stubs():
    """Score watchlist stub vessels (no AIS history)."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        from app.modules.risk_scoring import score_watchlist_stubs

        with console.status("[bold]Scoring watchlist stubs..."):
            result = score_watchlist_stubs(db)
        console.print(
            f"[green]Stub scoring complete:[/green] "
            f"scored={result.get('scored', 0)} cleared={result.get('cleared', 0)}"
        )
    finally:
        db.close()


@app.command("evaluate-detector")
def evaluate_detector(
    name: str = typer.Argument(..., help="Detector name (e.g. gap_detector, spoofing_detector)"),
    sample_size: int = typer.Option(50, "--sample-size", help="Number of anomalies to sample"),
):
    """Sample anomalies from a detector for holdout review.

    Outputs CSV to stdout: vessel_id, mmsi, anomaly_type, evidence_json,
    score_contribution, created_at, verdict (empty — operator fills in).
    """
    import csv
    import io

    from app.database import SessionLocal
    from app.models.spoofing_anomaly import SpoofingAnomaly
    from app.models.vessel import Vessel

    # Map detector name to anomaly types
    _DETECTOR_TYPE_MAP = {
        "gap_detector": ["AIS_GAP"],
        "spoofing_detector": [
            "ERRATIC_NAV_STATUS",
            "IMPOSSIBLE_POSITION",
            "CROSS_RECEIVER_DISAGREEMENT",
            "IDENTITY_SWAP",
            "FAKE_PORT_CALL",
        ],
        "track_naturalness": ["SYNTHETIC_TRACK"],
        "stateless_mmsi": ["STATELESS_MMSI"],
        "flag_hopping": ["FLAG_HOPPING"],
        "imo_fraud": ["IMO_FRAUD"],
        "draught": ["DRAUGHT_CHANGE"],
        "destination": ["DESTINATION_MISMATCH"],
        "sts_chain": ["STS_CHAIN"],
        "scrapped_registry": ["SCRAPPED_IMO_REUSE", "TRACK_REPLAY"],
        "fleet_analyzer": ["FLEET_PATTERN"],
        "convoy": ["CONVOY", "FLOATING_STORAGE", "ARCTIC_NO_ICE_CLASS"],
        "ownership_graph": ["SHELL_CHAIN", "CIRCULAR_OWNERSHIP", "SANCTIONS_PROPAGATION"],
    }

    anomaly_types = _DETECTOR_TYPE_MAP.get(name)
    if anomaly_types is None:
        console.print(f"[red]Unknown detector: {name}[/red]")
        console.print(f"[dim]Known detectors: {', '.join(sorted(_DETECTOR_TYPE_MAP))}[/dim]")
        raise typer.Exit(1)

    db = SessionLocal()
    try:
        query = (
            db.query(SpoofingAnomaly)
            .filter(SpoofingAnomaly.anomaly_type.in_(anomaly_types))
            .order_by(SpoofingAnomaly.spoofing_id.desc())
            .limit(sample_size)
        )
        anomalies = query.all()

        if not anomalies:
            console.print(f"[yellow]No anomalies found for detector: {name}[/yellow]")
            raise typer.Exit(0)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "vessel_id",
                "mmsi",
                "anomaly_type",
                "evidence_json",
                "score_contribution",
                "created_at",
                "verdict",
            ]
        )

        for a in anomalies:
            vessel = db.query(Vessel).filter(Vessel.vessel_id == a.vessel_id).first()
            mmsi = vessel.mmsi if vessel else "?"
            writer.writerow(
                [
                    a.vessel_id,
                    mmsi,
                    a.anomaly_type,
                    str(a.evidence_json) if a.evidence_json else "",
                    getattr(a, "risk_score_component", ""),
                    str(getattr(a, "created_at", "")) if getattr(a, "created_at", None) else "",
                    "",  # verdict — operator fills in
                ]
            )

        # Print CSV to stdout (not through Rich — raw output for piping)
        print(output.getvalue(), end="")
    finally:
        db.close()


@app.command("confirm-detector")
def confirm_detector(
    name: str = typer.Argument(..., help="Detector name"),
    holdout_csv: str = typer.Option(
        ..., "--holdout-csv", help="Path to reviewed CSV with verdicts"
    ),
):
    """Re-enable scoring after drift holdout review.

    Parses the CSV, computes precision = TP / (TP + FP) from the 'verdict'
    column. If precision >= 70%, clears the detector from drift-disabled list.
    """
    import csv

    from app.database import SessionLocal
    from app.models.pipeline_run import PipelineRun

    csv_path = Path(holdout_csv)
    if not csv_path.exists():
        console.print(f"[red]File not found: {holdout_csv}[/red]")
        raise typer.Exit(1)

    # Parse verdicts
    tp = fp = 0
    total_rows = 0
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            verdict = row.get("verdict", "").strip().upper()
            if verdict == "TP":
                tp += 1
                total_rows += 1
            elif verdict == "FP":
                fp += 1
                total_rows += 1
            # Skip rows without verdict

    if total_rows == 0:
        console.print("[red]No TP/FP verdicts found in CSV.[/red]")
        console.print("[dim]Mark the 'verdict' column as TP or FP for each row.[/dim]")
        raise typer.Exit(1)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    console.print(f"  TP={tp}  FP={fp}  Total={total_rows}  Precision={precision:.1%}")

    db = SessionLocal()
    try:
        if precision >= 0.70:
            # Clear detector from drift-disabled list in latest pipeline run
            latest_run = db.query(PipelineRun).order_by(PipelineRun.run_id.desc()).first()
            if latest_run and latest_run.drift_disabled_detectors_json:
                disabled = list(latest_run.drift_disabled_detectors_json)
                if name in disabled:
                    disabled.remove(name)
                    latest_run.drift_disabled_detectors_json = disabled
                    db.commit()
                    console.print(f"[green]Scoring re-enabled for {name}[/green]")
                else:
                    console.print(f"[dim]{name} was not in drift-disabled list.[/dim]")
            else:
                console.print("[dim]No pipeline run found or no disabled detectors.[/dim]")

            console.print(
                f"[green]Precision {precision:.1%} >= 70% threshold — {name} confirmed.[/green]"
            )
        else:
            console.print(
                f"[red]Precision {precision:.1%} below 70% threshold — "
                f"scoring stays disabled for {name}.[/red]\n"
                f"[dim]Investigate detector logic before re-enabling.[/dim]"
            )
            raise typer.Exit(1)
    finally:
        db.close()


@app.command("search")
def search_vessel(
    mmsi: str | None = typer.Option(None, "--mmsi"),
    imo: str | None = typer.Option(None, "--imo"),
    name: str | None = typer.Option(None, "--name"),
):
    """Find vessel by MMSI, IMO, or name and show watchlist status."""
    from app.database import SessionLocal
    from app.models.ais_point import AISPoint
    from app.models.vessel import Vessel
    from app.models.vessel_watchlist import VesselWatchlist

    db = SessionLocal()
    try:
        q = db.query(Vessel)
        if mmsi:
            q = q.filter(Vessel.mmsi == mmsi)
        elif imo:
            q = q.filter(Vessel.imo == imo)
        elif name:
            q = q.filter(Vessel.name.ilike(f"%{name}%"))
        else:
            console.print("[red]Provide --mmsi, --imo, or --name[/red]")
            raise typer.Exit(1)

        vessels = q.limit(10).all()
        if not vessels:
            console.print("[yellow]No vessels found[/yellow]")
            return

        for v in vessels:
            watchlist = (
                db.query(VesselWatchlist)
                .filter(VesselWatchlist.vessel_id == v.vessel_id, VesselWatchlist.is_active)
                .all()
            )
            last_point = (
                db.query(AISPoint)
                .filter(AISPoint.vessel_id == v.vessel_id)
                .order_by(AISPoint.timestamp_utc.desc())
                .first()
            )

            console.print(
                f"\n[bold cyan]MMSI:[/bold cyan] {v.mmsi}  [bold cyan]IMO:[/bold cyan] {v.imo}  [bold]Name:[/bold] {v.name}"
            )
            console.print(f"  Flag: {v.flag}  Type: {v.vessel_type}  DWT: {v.deadweight}")
            if watchlist:
                sources = ", ".join(w.watchlist_source for w in watchlist)
                console.print(f"  [bold red]WATCHLIST:[/bold red] {sources}")
            if last_point:
                console.print(
                    f"  Last seen: {last_point.timestamp_utc} at ({last_point.lat:.3f}, {last_point.lon:.3f})"
                )
    finally:
        db.close()


@app.command("gt-import")
def gt_import(
    source: str = typer.Argument(..., help="Source type: kse, ofac, or clean"),
    csv_path: str = typer.Argument(..., help="Path to CSV file"),
):
    """Import ground truth vessels from CSV (kse/ofac/clean)."""
    from app.database import SessionLocal
    from app.modules.ground_truth_loader import (
        link_ground_truth,
        load_clean_vessels_csv,
        load_kse_csv,
        load_ofac_sdn_csv,
    )

    db = SessionLocal()
    try:
        loaders = {"kse": load_kse_csv, "ofac": load_ofac_sdn_csv, "clean": load_clean_vessels_csv}
        loader = loaders.get(source.lower())
        if not loader:
            console.print(f"[red]Unknown source '{source}'. Use: kse, ofac, clean[/red]")
            raise typer.Exit(1)
        count = loader(db, csv_path)
        console.print(f"[green]Imported {count} ground truth records from {source}[/green]")
        linked = link_ground_truth(db)
        console.print(f"[green]Linked {linked} records to existing vessels[/green]")
    finally:
        db.close()


@app.command("validate")
def validate(
    threshold: str = typer.Option(
        "high", "--threshold", help="Band threshold for positive classification"
    ),
    source: str | None = typer.Option(None, "--source", help="Filter by ground truth source"),
    verbose: bool = typer.Option(False, "--verbose", help="Show per-vessel details"),
    signal_report: bool = typer.Option(
        False, "--signal-report", help="Show signal effectiveness report"
    ),
):
    """Run validation harness against ground truth data."""
    from app.database import SessionLocal
    from app.modules.validation_harness import run_validation, signal_effectiveness_report

    db = SessionLocal()
    try:
        result = run_validation(db, threshold_band=threshold)
        cm = result["confusion_matrix"]
        console.print(f"\n[bold]Validation Results[/bold] (threshold: {threshold})")
        console.print(f"  TP: {cm['tp']}  FP: {cm['fp']}")
        console.print(f"  FN: {cm['fn']}  TN: {cm['tn']}")
        console.print(f"  Precision: {result['precision']:.3f}")
        console.print(f"  Recall:    {result['recall']:.3f}")
        console.print(f"  F2:        {result['f2_score']:.3f}")
        if result.get("pr_auc") is not None:
            console.print(f"  PR-AUC:    {result['pr_auc']:.3f}")

        if signal_report:
            signals = signal_effectiveness_report(db)
            console.print("\n[bold]Signal Effectiveness[/bold]")
            for s in signals[:20]:
                lift_color = (
                    "green" if s["lift"] >= 1.5 else ("yellow" if s["lift"] >= 1.0 else "red")
                )
                console.print(
                    f"  [{lift_color}]{s['signal']:40s} lift={s['lift']:.2f}  TP_freq={s['tp_freq']:.0%}  FP_freq={s['fp_freq']:.0%}[/{lift_color}]"
                )
    finally:
        db.close()


@app.command("calibrate-lift-report")
def calibrate_lift_report():
    """Show lift-based signal weight adjustment proposals from analyst verdicts."""
    from app.database import SessionLocal
    from app.modules.fp_rate_tracker import generate_lift_based_suggestions

    db = SessionLocal()
    try:
        suggestions = generate_lift_based_suggestions(db)
        if not suggestions:
            console.print("[dim]No lift-based suggestions (insufficient verdicts or all signals in normal range).[/dim]")
            return

        console.print(f"\n[bold]Lift-Based Weight Adjustment Proposals ({len(suggestions)} signals)[/bold]\n")
        console.print(
            f"  {'Signal':<40s} {'Lift':>6s} {'Direction':>10s} {'Adj%':>7s} {'Weight':>7s} {'Config':>5s}"
        )
        console.print("  " + "-" * 80)

        for s in suggestions:
            lift_str = f"{s['lift']:.2f}" if isinstance(s["lift"], float) else str(s["lift"])
            adj_str = f"{s['suggested_adjustment_pct']:+.1f}%" if s["suggested_adjustment_pct"] is not None else "n/a"
            weight_str = str(s["current_weight"]) if s["current_weight"] is not None else "n/a"
            conf_str = "yes" if s["configurable"] else "no"
            direction = s["direction"] or ""

            lift_color = "red" if s["direction"] == "reduce" else "green"
            console.print(
                f"  [{lift_color}]{s['signal']:<40s} {lift_str:>6s} {direction:>10s} "
                f"{adj_str:>7s} {weight_str:>7s} {conf_str:>5s}[/{lift_color}]"
            )

        console.print()
        for s in suggestions:
            console.print(f"  [dim]{s['signal']}:[/dim] {s['reason']}")
    finally:
        db.close()


@app.command("watchlist-update")
def watchlist_update(
    force: bool = typer.Option(False, "--force", help="Ignore interval checks, update all sources"),
    source: str | None = typer.Option(
        None, "--source", help="Update a specific source only (OFAC_SDN, OPENSANCTIONS, KSE_SHADOW)"
    ),
):
    """Run watchlist auto-update (OFAC daily, OpenSanctions daily, KSE weekly)."""
    from app.database import SessionLocal, init_db
    from app.modules.watchlist_scheduler import run_watchlist_update

    init_db()
    db = SessionLocal()
    try:
        sources = [source] if source else None
        results = run_watchlist_update(db, force=force, sources=sources)

        for r in results:
            status = r.get("status", "unknown")
            name = r.get("source", "?")
            if status == "success":
                console.print(
                    f"[green]{name}:[/green] +{r.get('added', 0)} added, "
                    f"-{r.get('removed', 0)} removed, "
                    f"{r.get('unchanged', 0)} unchanged"
                )
            elif status == "skipped":
                console.print(f"[dim]{name}: skipped (within interval)[/dim]")
            else:
                console.print(f"[red]{name}: {r.get('error', 'unknown error')}[/red]")
    finally:
        db.close()
