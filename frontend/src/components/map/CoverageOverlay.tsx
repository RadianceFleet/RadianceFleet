import { GeoJSON } from "react-leaflet";
import { useCoverageGeoJSON } from "../../hooks/useCoverage";
import type { PathOptions } from "leaflet";

const QUALITY_COLORS: Record<string, string> = {
  GOOD: "#16a34a", // green
  MODERATE: "#d97706", // yellow/amber
  PARTIAL: "#ea580c", // orange
  POOR: "#dc2626", // red
  NONE: "#6b7280", // gray
  UNKNOWN: "#6b7280", // gray
};

function coverageStyle(feature: GeoJSON.Feature | undefined): PathOptions {
  const quality = feature?.properties?.quality ?? "UNKNOWN";
  return {
    color: QUALITY_COLORS[quality] ?? "#6b7280",
    weight: 1,
    opacity: 0.6,
    fillOpacity: 0.12,
    dashArray: "4 4",
  };
}

export function CoverageOverlay() {
  const { data: geojson } = useCoverageGeoJSON();

  if (!geojson || geojson.features.length === 0) return null;

  return (
    <GeoJSON
      key={geojson.features.length}
      data={geojson as GeoJSON.GeoJsonObject}
      style={coverageStyle}
      onEachFeature={(feature, layer) => {
        const props = feature.properties;
        if (props) {
          let tooltip = `<b>${props.name}</b>: ${props.quality}<br/>${props.description}`;
          if (props.suggested_sources) {
            tooltip += `<br/><br/><i>Suggested sources:</i><br/>${props.suggested_sources}`;
          }
          layer.bindPopup(tooltip, { maxWidth: 300 });
          layer.bindTooltip(`${props.name}: ${props.quality}`, { sticky: true });
        }
      }}
    />
  );
}
