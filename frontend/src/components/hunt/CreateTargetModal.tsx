import { useState } from "react";
import { useCreateHuntTarget } from "../../hooks/useHunt";
import { Card } from "../ui/Card";

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.5)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 9999,
};

const labelStyle: React.CSSProperties = {
  fontSize: "0.75rem",
  fontWeight: 600,
  color: "var(--text-muted)",
  marginBottom: "0.25rem",
};

const inputStyle: React.CSSProperties = {
  padding: "0.375rem 0.5rem",
  background: "var(--bg-base)",
  color: "var(--text-body)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  fontSize: "0.8125rem",
  width: "100%",
  boxSizing: "border-box",
};

const btnBase: React.CSSProperties = {
  padding: "0.375rem 0.75rem",
  fontSize: "0.8125rem",
  fontWeight: 600,
  borderRadius: "var(--radius)",
  cursor: "pointer",
  border: "none",
};

export function CreateTargetModal({ onClose }: { onClose: () => void }) {
  const [vesselId, setVesselId] = useState("");
  const [lastLat, setLastLat] = useState("");
  const [lastLon, setLastLon] = useState("");
  const mutation = useCreateHuntTarget();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const vid = parseInt(vesselId, 10);
    if (isNaN(vid)) return;
    const body: { vessel_id: number; last_lat?: number; last_lon?: number } = { vessel_id: vid };
    if (lastLat) body.last_lat = parseFloat(lastLat);
    if (lastLon) body.last_lon = parseFloat(lastLon);
    mutation.mutate(body, { onSuccess: () => onClose() });
  }

  return (
    <div style={overlayStyle} onClick={onClose}>
      <Card style={{ width: 400, maxHeight: "80vh", overflow: "auto" }}>
        <form onSubmit={handleSubmit} onClick={(e) => e.stopPropagation()}>
          <h3 style={{ margin: "0 0 1rem", fontSize: "0.9375rem", color: "var(--text-bright)" }}>
            New Hunt Target
          </h3>

          <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
            <div>
              <div style={labelStyle}>Vessel ID *</div>
              <input
                type="number"
                required
                value={vesselId}
                onChange={(e) => setVesselId(e.target.value)}
                style={inputStyle}
                placeholder="e.g. 42"
              />
            </div>
            <div>
              <div style={labelStyle}>Last Known Latitude</div>
              <input
                type="number"
                step="any"
                value={lastLat}
                onChange={(e) => setLastLat(e.target.value)}
                style={inputStyle}
                placeholder="e.g. 59.3456"
              />
            </div>
            <div>
              <div style={labelStyle}>Last Known Longitude</div>
              <input
                type="number"
                step="any"
                value={lastLon}
                onChange={(e) => setLastLon(e.target.value)}
                style={inputStyle}
                placeholder="e.g. 24.7890"
              />
            </div>
          </div>

          {mutation.isError && (
            <p
              style={{
                color: "var(--score-critical)",
                fontSize: "0.8125rem",
                margin: "0.75rem 0 0",
              }}
            >
              Failed to create target. Please try again.
            </p>
          )}

          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              gap: "0.5rem",
              marginTop: "1rem",
            }}
          >
            <button
              type="button"
              onClick={onClose}
              style={{
                ...btnBase,
                background: "transparent",
                color: "var(--text-muted)",
                border: "1px solid var(--border)",
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={mutation.isPending}
              style={{
                ...btnBase,
                background: "var(--accent-primary)",
                color: "#fff",
                opacity: mutation.isPending ? 0.6 : 1,
              }}
            >
              {mutation.isPending ? "Creating..." : "Create Target"}
            </button>
          </div>
        </form>
      </Card>
    </div>
  );
}
