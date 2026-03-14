import { CircleMarker, Popup } from "react-leaflet";
import { useGlobalLoitering } from "../../hooks/useLoitering";

export function LoiteringOverlay() {
  const { data } = useGlobalLoitering({ limit: 200 });
  const items = data?.items ?? [];

  return (
    <>
      {items
        .filter((e) => e.mean_lat != null && e.mean_lon != null)
        .map((e) => {
          const hours = e.duration_hours ?? 0;
          const radius = Math.min(20, Math.max(6, hours / 4));
          const color = hours >= 48 ? "#dc2626" : "#d97706";

          return (
            <CircleMarker
              key={e.loiter_id}
              center={[e.mean_lat!, e.mean_lon!]}
              radius={radius}
              pathOptions={{ color, fillColor: color, fillOpacity: 0.35, weight: 2 }}
            >
              <Popup>
                <div style={{ fontSize: 13, fontFamily: "monospace" }}>
                  <b>Loitering</b>
                  <br />
                  Vessel ID: {e.vessel_id}
                  <br />
                  Duration: {hours.toFixed(1)}h<br />
                  Start: {e.start_time_utc?.slice(0, 16).replace("T", " ") ?? "-"} UTC
                </div>
              </Popup>
            </CircleMarker>
          );
        })}
    </>
  );
}
