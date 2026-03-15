import { Marker, Popup } from "react-leaflet";
import L from "leaflet";
import { useSarDetections } from "../../hooks/useSarDetections";

function makeDiamondIcon(size: number = 14) {
  const half = size / 2;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
    <rect x="${half}" y="0" width="${half}" height="${half}" transform="rotate(45 ${half} ${half})"
      fill="#3b82f6" stroke="#1e40af" stroke-width="1.5"/>
  </svg>`;
  return L.divIcon({
    className: "",
    html: `<div style="width:${size}px;height:${size}px;filter:drop-shadow(0 0 2px rgba(0,0,0,.5))">${svg}</div>`,
    iconSize: [size, size],
    iconAnchor: [half, half],
  });
}

const diamondIcon = makeDiamondIcon(16);

export function SarOverlay() {
  const { data } = useSarDetections();
  const items = data ?? [];

  return (
    <>
      {items
        .filter((d) => d.latitude != null && d.longitude != null)
        .map((d) => (
          <Marker
            key={d.detection_id}
            position={[d.latitude!, d.longitude!]}
            icon={diamondIcon}
          >
            <Popup>
              <div style={{ fontSize: 13, fontFamily: "monospace" }}>
                <b>SAR Detection</b>
                <br />
                Detection ID: {d.detection_id}
                <br />
                Time: {d.detection_timestamp_utc?.slice(0, 16).replace("T", " ") ?? "-"} UTC
                <br />
                Length: {d.estimated_length_m != null ? `${d.estimated_length_m.toFixed(0)}m` : "-"}
                <br />
                Type: {d.vessel_type_estimate ?? "Unknown"}
                <br />
                Confidence: {d.confidence != null ? `${(d.confidence * 100).toFixed(0)}%` : "-"}
              </div>
            </Popup>
          </Marker>
        ))}
    </>
  );
}
