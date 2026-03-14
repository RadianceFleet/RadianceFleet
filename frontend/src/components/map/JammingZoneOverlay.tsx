import { GeoJSON } from "react-leaflet";
import { useJammingZonesGeoJSON } from "../../hooks/useJammingZones";
import type { PathOptions } from "leaflet";

const STATUS_COLORS: Record<string, string> = {
  active: "#dc2626",    // red
  decaying: "#ea580c",  // orange
  expired: "#6b7280",   // gray
};

function jammingZoneStyle(feature: GeoJSON.Feature | undefined): PathOptions {
  const status = feature?.properties?.status ?? "expired";
  const confidence = feature?.properties?.confidence ?? 0.5;
  return {
    color: STATUS_COLORS[status] ?? "#6b7280",
    weight: 2,
    opacity: 0.8,
    fillOpacity: Math.max(0.08, confidence * 0.3),
    dashArray: status === "decaying" ? "6 4" : undefined,
  };
}

export function JammingZoneOverlay() {
  const { data: geojson } = useJammingZonesGeoJSON();

  if (!geojson || geojson.features.length === 0) return null;

  return (
    <GeoJSON
      key={geojson.features.length}
      data={geojson as GeoJSON.GeoJsonObject}
      style={jammingZoneStyle}
      onEachFeature={(feature, layer) => {
        const props = feature.properties;
        if (props) {
          const tooltip = [
            `<b>Jamming Zone #${props.zone_id}</b>`,
            `Status: <b>${props.status}</b>`,
            `Confidence: ${(props.confidence * 100).toFixed(0)}%`,
            `Vessels affected: ${props.vessel_count}`,
            `Gap events: ${props.gap_count}`,
            `Radius: ${props.radius_nm?.toFixed(1)} nm`,
            props.first_detected_at
              ? `First detected: ${new Date(props.first_detected_at).toLocaleDateString()}`
              : "",
            props.last_gap_at
              ? `Last gap: ${new Date(props.last_gap_at).toLocaleDateString()}`
              : "",
          ]
            .filter(Boolean)
            .join("<br/>");
          layer.bindPopup(tooltip, { maxWidth: 300 });
          layer.bindTooltip(
            `Jamming Zone #${props.zone_id}: ${props.status}`,
            { sticky: true }
          );
        }
      }}
    />
  );
}
