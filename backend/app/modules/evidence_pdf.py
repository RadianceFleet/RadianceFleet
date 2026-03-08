"""PDF evidence card generation using fpdf2.

Produces a downloadable PDF report for a single AIS gap alert,
mirroring the data in the JSON/Markdown evidence card exports.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fpdf import FPDF
from fpdf.fonts import FontFace
from sqlalchemy.orm import Session

from app.models.gap_event import AISGapEvent
from app.models.vessel import Vessel
from app.modules.evidence_export import DISCLAIMER, _build_card

logger = logging.getLogger(__name__)


def export_evidence_pdf(alert_id: int, db: Session) -> bytes:
    """Generate a PDF evidence card for the given alert.

    Returns raw PDF bytes.  Raises ``ValueError`` if the alert has not
    been reviewed (status == "new").
    """
    gap = db.query(AISGapEvent).filter(AISGapEvent.gap_event_id == alert_id).first()
    if not gap:
        raise ValueError("Alert not found")

    if gap.status == "new":
        raise ValueError(
            "Evidence card cannot be exported without analyst review. "
            "Set alert status before exporting."
        )

    vessel = db.query(Vessel).filter(Vessel.vessel_id == gap.vessel_id).first()
    from app.models.corridor import Corridor

    corridor = (
        db.query(Corridor).filter(Corridor.corridor_id == gap.corridor_id).first()
        if gap.corridor_id
        else None
    )
    card = _build_card(gap, vessel, corridor=corridor, db=db)

    return _render_pdf(card)


def _render_pdf(card: dict) -> bytes:
    """Build the PDF document from a card data dict and return bytes."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Try to load DejaVu for Unicode support; fall back to Helvetica
    try:
        pdf.add_font(fname="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        font_family = "DejaVuSans"
    except Exception:
        font_family = "Helvetica"

    # ── Title ─────────────────────────────────────────────────────────
    pdf.set_font(font_family, size=16)
    pdf.cell(
        text=f"RadianceFleet Evidence Card -- Alert #{card['alert_id']}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)

    pdf.set_font(font_family, size=10)
    pdf.cell(
        text=f"Exported: {card.get('exported_at', datetime.now(UTC).isoformat())}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.cell(text=f"Status: {card.get('status', 'N/A')}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # ── Vessel ────────────────────────────────────────────────────────
    _section_heading(pdf, font_family, "Vessel")
    v = card.get("vessel", {})
    for label, key in [
        ("MMSI", "mmsi"),
        ("IMO", "imo"),
        ("Name", "name"),
        ("Flag", "flag"),
        ("Type", "vessel_type"),
    ]:
        pdf.cell(text=f"  {label}: {_safe(v.get(key))}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # ── AIS Gap ───────────────────────────────────────────────────────
    _section_heading(pdf, font_family, "AIS Gap")
    g = card.get("gap", {})
    pdf.cell(text=f"  Start: {_safe(g.get('start_utc'))}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(text=f"  End: {_safe(g.get('end_utc'))}", new_x="LMARGIN", new_y="NEXT")
    dur = g.get("duration_minutes")
    dur_str = f"{dur} min ({dur / 60:.1f}h)" if dur else "N/A"
    pdf.cell(text=f"  Duration: {dur_str}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # ── Risk Score ────────────────────────────────────────────────────
    _section_heading(pdf, font_family, "Risk Score")
    r = card.get("risk", {})
    pdf.cell(text=f"  Total Score: {_safe(r.get('score'))}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    breakdown = r.get("breakdown") or {}
    if breakdown:
        pdf.set_font(font_family, size=9)
        with pdf.table(col_widths=(90, 30), headings_style=FontFace(emphasis="")) as table:
            header = table.row()
            header.cell("Signal")
            header.cell("Points")
            for signal, pts in breakdown.items():
                row = table.row()
                row.cell(str(signal))
                row.cell(str(pts))
        pdf.set_font(font_family, size=10)
    pdf.ln(4)

    # ── Movement Envelope ─────────────────────────────────────────────
    _section_heading(pdf, font_family, "Movement Envelope")
    env = card.get("movement_envelope", {})
    max_d = env.get("max_plausible_distance_nm")
    act_d = env.get("actual_gap_distance_nm")
    vel_r = env.get("velocity_plausibility_ratio")
    pdf.cell(
        text=f"  Max plausible distance: {f'{max_d:.1f} nm' if max_d is not None else 'N/A'}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.cell(
        text=f"  Actual gap distance: {f'{act_d:.1f} nm' if act_d is not None else 'N/A'}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.cell(
        text=f"  Velocity ratio: {f'{vel_r:.2f}' if vel_r is not None else 'N/A'}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.cell(
        text=f"  Impossible speed flag: {_safe(env.get('impossible_speed_flag'))}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)

    # ── AIS Boundary Points ───────────────────────────────────────────
    _section_heading(pdf, font_family, "AIS Boundary Points")
    lkp = card.get("last_known_position")
    if lkp:
        pdf.cell(
            text=f"  Last known: {lkp['lat']}, {lkp['lon']} at {lkp['timestamp_utc']}  SOG={lkp['sog']} COG={lkp['cog']}",
            new_x="LMARGIN",
            new_y="NEXT",
        )
    else:
        pdf.cell(text="  Last known position: unavailable", new_x="LMARGIN", new_y="NEXT")

    fpa = card.get("first_position_after_gap")
    if fpa:
        pdf.cell(
            text=f"  First after gap: {fpa['lat']}, {fpa['lon']} at {fpa['timestamp_utc']}  SOG={fpa['sog']} COG={fpa['cog']}",
            new_x="LMARGIN",
            new_y="NEXT",
        )
    else:
        pdf.cell(text="  First position after gap: unavailable", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # ── Coverage ──────────────────────────────────────────────────────
    _section_heading(pdf, font_family, "Data Source Coverage")
    quality, coverage_desc = card.get("coverage", ("UNKNOWN", "No coverage data"))
    corridor_name = card.get("corridor_name") or "Unknown"
    pdf.cell(text=f"  Region: {corridor_name}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(text=f"  AIS Coverage Quality: {quality}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(text=f"  Notes: {_truncate(str(coverage_desc), 200)}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # ── Analyst Notes ─────────────────────────────────────────────────
    _section_heading(pdf, font_family, "Analyst Notes")
    notes = card.get("analyst_notes") or "No notes"
    pdf.set_font(font_family, size=9)
    pdf.multi_cell(w=0, text=_truncate(str(notes), 500))
    pdf.ln(4)

    # ── Disclaimer ────────────────────────────────────────────────────
    pdf.set_font(font_family, size=8)
    pdf.multi_cell(w=0, text=DISCLAIMER)

    return bytes(pdf.output())


def _section_heading(pdf: FPDF, font_family: str, title: str) -> None:
    """Render a bold-ish section heading."""
    pdf.set_font(font_family, size=12)
    pdf.cell(text=title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(font_family, size=10)


def _safe(value) -> str:
    """Convert a value to a safe string for PDF rendering."""
    if value is None:
        return "N/A"
    return str(value)


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to prevent page overflow."""
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text
