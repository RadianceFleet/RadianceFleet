import { Marker, Popup } from "react-leaflet";
import L from "leaflet";
import { useDarkVessels } from "../../hooks/useDarkVessels";

const triangleIcon = L.divIcon({
  className: "",
  html: `<div style="width:0;height:0;border-left:7px solid transparent;border-right:7px solid transparent;border-bottom:14px solid #9333ea;filter:drop-shadow(0 0 2px rgba(0,0,0,.5))"></div>`,
  iconSize: [14, 14],
  iconAnchor: [7, 14],
});

interface DarkVesselOverlayProps {
  excludeViirs?: boolean;
  excludeSar?: boolean;
}

export function DarkVesselOverlay({ excludeViirs = false, excludeSar = false }: DarkVesselOverlayProps) {
  const { data } = useDarkVessels({ limit: 200 });
  const items = (data?.items ?? []).filter((d) => {
    if (d.detection_lat == null || d.detection_lon == null) return false;
    if (excludeViirs && d.scene_id?.startsWith("viirs-")) return false;
    if (excludeSar && d.scene_id?.startsWith("gfw-sar-")) return false;
    return true;
  });

  return (
    <>
      {items.map((d) => (
        <Marker
          key={d.detection_id}
          position={[d.detection_lat!, d.detection_lon!]}
          icon={triangleIcon}
        >
          <Popup>
            <div style={{ fontSize: 13, fontFamily: "monospace" }}>
              <b>Dark Vessel</b>
              <br />
              Detection ID: {d.detection_id}
              <br />
              Confidence:{" "}
              {d.model_confidence != null ? `${(d.model_confidence * 100).toFixed(0)}%` : "-"}
              <br />
              Type: {d.vessel_type_inferred ?? "Unknown"}
            </div>
          </Popup>
        </Marker>
      ))}
    </>
  );
}
