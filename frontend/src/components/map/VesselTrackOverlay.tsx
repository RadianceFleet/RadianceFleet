import { GeoJSON, CircleMarker, Popup } from "react-leaflet";
import { useVesselTrack } from "../../hooks/useVessels";
import type { PathOptions } from "leaflet";

const trackStyle: PathOptions = {
  color: "#3b82f6",
  weight: 3,
  opacity: 0.8,
};

interface Props {
  vesselId: string | number;
  vesselName?: string;
}

export function VesselTrackOverlay({ vesselId, vesselName }: Props) {
  const { data: geojson, isLoading } = useVesselTrack(vesselId);

  if (isLoading || !geojson || !geojson.features?.length) return null;

  const feature = geojson.features[0];
  const points = feature.properties?.point_data ?? [];

  return (
    <>
      <GeoJSON key={`track-${vesselId}`} data={geojson} style={() => trackStyle} />
      {points.slice(0, 200).map((pt: GeoJSON.Feature, i: number) => {
        const coords = feature.geometry?.coordinates?.[i];
        if (!coords) return null;
        return (
          <CircleMarker
            key={i}
            center={[coords[1], coords[0]]}
            radius={2}
            pathOptions={{ color: "#3b82f6", fillColor: "#3b82f6", fillOpacity: 0.6 }}
          >
            <Popup>
              <div style={{ fontSize: 12, fontFamily: "monospace" }}>
                {vesselName && <b>{vesselName}</b>}
                {pt.timestamp && (
                  <>
                    <br />
                    {pt.timestamp.slice(0, 16).replace("T", " ")} UTC
                  </>
                )}
                {pt.sog != null && (
                  <>
                    <br />
                    SOG: {pt.sog} kn
                  </>
                )}
              </div>
            </Popup>
          </CircleMarker>
        );
      })}
    </>
  );
}
