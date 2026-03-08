"""Vessel track export in GeoJSON and KML formats."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime

from sqlalchemy.orm import Session


def _query_points(
    db: Session, vessel_id: int, date_from: date | None = None, date_to: date | None = None
):
    """Query AISPoint records for a vessel, ordered by timestamp."""
    from app.models.ais_point import AISPoint

    q = db.query(AISPoint).filter(AISPoint.vessel_id == vessel_id)
    if date_from:
        q = q.filter(
            AISPoint.timestamp_utc >= datetime(date_from.year, date_from.month, date_from.day)
        )
    if date_to:
        q = q.filter(
            AISPoint.timestamp_utc <= datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59)
        )
    return q.order_by(AISPoint.timestamp_utc).all()


def export_track_geojson(
    db: Session,
    vessel_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Build a GeoJSON FeatureCollection with a single LineString feature for the vessel track."""
    points = _query_points(db, vessel_id, date_from, date_to)

    coordinates = [[p.lon, p.lat] for p in points]
    timestamps = [p.timestamp_utc.isoformat() if p.timestamp_utc else None for p in points]
    point_data = [
        {
            "timestamp": p.timestamp_utc.isoformat() if p.timestamp_utc else None,
            "sog": p.sog,
            "cog": p.cog,
        }
        for p in points
    ]

    geometry = None
    if coordinates:
        geometry = {"type": "LineString", "coordinates": coordinates}

    feature = {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "vessel_id": vessel_id,
            "point_count": len(points),
            "timestamps": timestamps,
            "point_data": point_data,
        },
    }

    return {"type": "FeatureCollection", "features": [feature]}


def export_track_kml(
    db: Session,
    vessel_id: int,
    vessel_name: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> str:
    """Build a KML string with a gx:Track for the vessel track."""
    KML_NS = "http://www.opengis.net/kml/2.2"
    GX_NS = "http://www.google.com/kml/ext/2.2"

    ET.register_namespace("", KML_NS)
    ET.register_namespace("gx", GX_NS)

    kml = ET.Element("kml", xmlns=KML_NS)
    document = ET.SubElement(kml, "Document")
    name_el = ET.SubElement(document, "name")
    name_el.text = vessel_name

    placemark = ET.SubElement(document, "Placemark")
    pm_name = ET.SubElement(placemark, "name")
    pm_name.text = vessel_name

    track = ET.SubElement(placemark, f"{{{GX_NS}}}Track")

    points = _query_points(db, vessel_id, date_from, date_to)
    for p in points:
        when = ET.SubElement(track, "when")
        when.text = p.timestamp_utc.isoformat() if p.timestamp_utc else ""
        coord = ET.SubElement(track, f"{{{GX_NS}}}coord")
        coord.text = f"{p.lon} {p.lat} 0"

    tree = ET.ElementTree(kml)
    import io

    buf = io.BytesIO()
    tree.write(buf, xml_declaration=True, encoding="UTF-8")
    return buf.getvalue().decode("UTF-8")
