import { CircleMarker, Popup } from "react-leaflet";
import { useViirsDetections } from "../../hooks/useViirsDetections";

export function ViirsOverlay() {
  const { data } = useViirsDetections();
  const items = data ?? [];

  return (
    <>
      {items
        .filter((d) => d.latitude != null && d.longitude != null)
        .map((d) => {
          const radius = 4 + (d.confidence ?? 0.5) * 10;
          return (
            <CircleMarker
              key={d.detection_id}
              center={[d.latitude!, d.longitude!]}
              radius={radius}
              pathOptions={{
                color: "#c2410c",
                fillColor: "#f97316",
                fillOpacity: 0.7,
                weight: 2,
              }}
            >
              <Popup>
                <div style={{ fontSize: 13, fontFamily: "monospace" }}>
                  <b>VIIRS Nightlight</b>
                  <br />
                  Detection ID: {d.detection_id}
                  <br />
                  Time: {d.detection_timestamp_utc?.slice(0, 16).replace("T", " ") ?? "-"} UTC
                  <br />
                  Lat: {d.latitude?.toFixed(4)}, Lon: {d.longitude?.toFixed(4)}
                  <br />
                  Confidence: {d.confidence != null ? `${(d.confidence * 100).toFixed(0)}%` : "-"}
                  <br />
                  Matched Vessel: {d.matched_vessel_id ?? "None"}
                </div>
              </Popup>
            </CircleMarker>
          );
        })}
    </>
  );
}
