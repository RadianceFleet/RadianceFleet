import { useState, useEffect } from "react";
import { apiFetch } from "../lib/api";

interface CalibrationEvent {
  timestamp: string;
  action: string;
  description: string;
  multiplier_before: number | null;
  multiplier_after: number | null;
  analyst_name: string | null;
}

interface Props {
  corridorId: number | null;
}

export function CalibrationTimeline({ corridorId }: Props) {
  const [events, setEvents] = useState<CalibrationEvent[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!corridorId) {
      setEvents([]);
      return;
    }
    setLoading(true);
    apiFetch<CalibrationEvent[]>(
      `/corridors/${corridorId}/calibration-history`
    )
      .then(setEvents)
      .catch(() => setEvents([]))
      .finally(() => setLoading(false));
  }, [corridorId]);

  if (!corridorId) {
    return <p style={{ color: "#6b7280" }}>Select a region to view calibration history.</p>;
  }

  if (loading) {
    return <p>Loading calibration history...</p>;
  }

  if (events.length === 0) {
    return <p>No calibration history for this corridor.</p>;
  }

  return (
    <div data-testid="calibration-timeline" style={{ position: "relative", paddingLeft: "1.5rem" }}>
      {/* vertical line */}
      <div
        style={{
          position: "absolute",
          left: "0.5rem",
          top: 0,
          bottom: 0,
          width: 2,
          backgroundColor: "#d1d5db",
        }}
      />
      {events.map((ev, i) => (
        <div key={i} style={{ position: "relative", marginBottom: "1.25rem" }}>
          {/* dot */}
          <div
            style={{
              position: "absolute",
              left: "-1.25rem",
              top: "0.25rem",
              width: 10,
              height: 10,
              borderRadius: "50%",
              backgroundColor: "#3b82f6",
              border: "2px solid white",
            }}
          />
          <div style={{ fontSize: "0.75rem", color: "#6b7280" }}>
            {new Date(ev.timestamp).toLocaleString()}
            {ev.analyst_name && ` by ${ev.analyst_name}`}
          </div>
          <div style={{ fontWeight: 600, marginTop: "0.125rem" }}>
            {ev.action}
          </div>
          <div style={{ fontSize: "0.875rem", color: "#374151" }}>
            {ev.description}
          </div>
          {ev.multiplier_before != null && ev.multiplier_after != null && (
            <div style={{ fontSize: "0.75rem", color: "#6b7280", marginTop: "0.125rem" }}>
              Multiplier: {ev.multiplier_before}x &rarr; {ev.multiplier_after}x
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
