// Self-contained vessel risk card for embedding in external sites
import { useState, useEffect } from "react";

interface VesselRiskData {
  vessel_id: number;
  name: string;
  mmsi: string;
  flag: string;
  risk_score: number;
  risk_band: string;
  last_seen: string;
}

export function VesselCard({ vesselId, apiUrl }: { vesselId: number; apiUrl: string }) {
  const [data, setData] = useState<VesselRiskData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch(`${apiUrl}/api/v1/vessels/${vesselId}`, {
          headers: { "X-API-Key": new URLSearchParams(window.location.search).get("key") || "" },
        });
        if (!r.ok) {
          const body = await r.json().catch(() => ({ detail: r.statusText }));
          throw new Error(body.detail ?? `HTTP ${r.status}`);
        }
        const d = await r.json();
        setData({
          vessel_id: d.vessel_id,
          name: d.name || "Unknown",
          mmsi: d.mmsi || "",
          flag: d.flag || "",
          risk_score: d.last_risk_score ?? 0,
          risk_band:
            d.last_risk_score >= 76
              ? "CRITICAL"
              : d.last_risk_score >= 51
                ? "HIGH"
                : d.last_risk_score >= 21
                  ? "MEDIUM"
                  : "LOW",
          last_seen: d.last_ais_time || "",
        });
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load vessel");
      }
    };
    load();
  }, [vesselId, apiUrl]);

  if (error)
    return (
      <div style={cardStyle}>
        <p>Error loading vessel data</p>
      </div>
    );
  if (!data)
    return (
      <div style={cardStyle}>
        <p>Loading...</p>
      </div>
    );

  const bandColor =
    data.risk_band === "CRITICAL"
      ? "#dc2626"
      : data.risk_band === "HIGH"
        ? "#ea580c"
        : data.risk_band === "MEDIUM"
          ? "#d97706"
          : "#16a34a";

  return (
    <div style={cardStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 8,
        }}
      >
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{data.name}</h3>
        <span
          style={{
            background: bandColor,
            color: "#fff",
            padding: "2px 8px",
            borderRadius: 4,
            fontSize: 11,
            fontWeight: 700,
          }}
        >
          {data.risk_band}
        </span>
      </div>
      <div style={{ fontSize: 12, color: "#64748b", lineHeight: 1.6 }}>
        <div>MMSI: {data.mmsi}</div>
        <div>Flag: {data.flag}</div>
        <div>
          Risk Score: <b>{data.risk_score}</b>
        </div>
        {data.last_seen && (
          <div>Last Seen: {data.last_seen.slice(0, 16).replace("T", " ")} UTC</div>
        )}
      </div>
      <div style={{ marginTop: 8, fontSize: 10, color: "#94a3b8", textAlign: "right" as const }}>
        Powered by RadianceFleet
      </div>
    </div>
  );
}

const cardStyle: React.CSSProperties = {
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  background: "#0f172a",
  color: "#e2e8f0",
  borderRadius: 8,
  padding: 16,
  maxWidth: 320,
  boxShadow: "0 4px 6px -1px rgba(0,0,0,.3)",
  border: "1px solid #1e293b",
};
