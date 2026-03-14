import type { NetworkNode } from "../hooks/useOwnershipNetwork";

interface NetworkNodeTooltipProps {
  node: NetworkNode;
  x: number;
  y: number;
}

const layerLabels: Record<number, string> = {
  0: "Root Company",
  1: "Intermediary",
  2: "Leaf Owner",
  3: "Vessel",
};

export function NetworkNodeTooltip({ node, x, y }: NetworkNodeTooltipProps) {
  return (
    <div
      data-testid="network-tooltip"
      style={{
        position: "absolute",
        left: x + 12,
        top: y - 8,
        background: "var(--bg-surface, #1e293b)",
        border: "1px solid var(--border-dim, #334155)",
        borderRadius: 6,
        padding: "8px 12px",
        fontSize: 12,
        color: "var(--text-body, #e2e8f0)",
        pointerEvents: "none",
        zIndex: 1000,
        maxWidth: 260,
        boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{node.label}</div>
      <div style={{ color: "var(--text-muted, #94a3b8)" }}>
        {layerLabels[node.layer] ?? "Unknown"} &middot; {node.type}
      </div>
      {node.jurisdiction && (
        <div style={{ marginTop: 2 }}>Jurisdiction: {node.jurisdiction}</div>
      )}
      {node.is_sanctioned && (
        <div style={{ color: "#ef4444", marginTop: 2, fontWeight: 600 }}>
          Sanctioned
        </div>
      )}
      {node.is_spv && (
        <div style={{ color: "#f59e0b", marginTop: 2, fontWeight: 600 }}>
          SPV Detected
        </div>
      )}
      {node.vessel_id != null && (
        <div style={{ marginTop: 2 }}>Vessel ID: {node.vessel_id}</div>
      )}
    </div>
  );
}
