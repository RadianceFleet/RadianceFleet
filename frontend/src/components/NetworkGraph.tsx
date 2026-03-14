import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import type { NetworkNode, NetworkEdge } from "../hooks/useOwnershipNetwork";
import { NetworkNodeTooltip } from "./NetworkNodeTooltip";

interface NetworkGraphProps {
  nodes: NetworkNode[];
  edges: NetworkEdge[];
  sanctionsPaths?: string[][];
}

// Layout constants
const LAYER_Y: Record<number, number> = { 0: 60, 1: 180, 2: 300, 3: 420 };
const NODE_SPACING_X = 140;
const PADDING_X = 80;
const RECT_W = 110;
const RECT_H = 36;
const CIRCLE_R = 22;

function getNodeColor(node: NetworkNode): string {
  if (node.is_sanctioned) return "#ef4444";
  if (node.is_spv) return "#f59e0b";
  return "#3b82f6";
}

function getNodeStroke(node: NetworkNode, isHighlighted: boolean): string {
  if (isHighlighted) return "#ffffff";
  return getNodeColor(node);
}

export function NetworkGraph({ nodes, edges, sanctionsPaths = [] }: NetworkGraphProps) {
  const navigate = useNavigate();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredNode, setHoveredNode] = useState<{
    node: NetworkNode;
    x: number;
    y: number;
  } | null>(null);

  // Compute positions: group by layer, spread horizontally
  const positioned = useMemo(() => {
    const byLayer: Record<number, NetworkNode[]> = {};
    for (const node of nodes) {
      const layer = node.layer;
      if (!byLayer[layer]) byLayer[layer] = [];
      byLayer[layer].push(node);
    }

    const positions = new Map<string, { x: number; y: number }>();
    for (const [layerStr, layerNodes] of Object.entries(byLayer)) {
      const layer = Number(layerStr);
      const y = LAYER_Y[layer] ?? 60 + layer * 120;
      const totalWidth = (layerNodes.length - 1) * NODE_SPACING_X;
      const startX = PADDING_X + Math.max(0, (500 - totalWidth) / 2);
      layerNodes.forEach((node, i) => {
        positions.set(node.id, { x: startX + i * NODE_SPACING_X, y });
      });
    }
    return positions;
  }, [nodes]);

  // Highlighted path (nodes connected to selected)
  const highlightedIds = useMemo(() => {
    if (!selectedId) return new Set<string>();
    const ids = new Set<string>([selectedId]);
    // Find connected via sanctions paths
    for (const path of sanctionsPaths) {
      if (path.includes(selectedId)) {
        path.forEach((id) => ids.add(id));
      }
    }
    // Also highlight direct neighbors
    for (const edge of edges) {
      if (edge.source === selectedId) ids.add(edge.target);
      if (edge.target === selectedId) ids.add(edge.source);
    }
    return ids;
  }, [selectedId, edges, sanctionsPaths]);

  if (nodes.length === 0) {
    return (
      <div data-testid="network-empty" style={{ padding: 24, textAlign: "center", color: "var(--text-muted, #94a3b8)" }}>
        No ownership data available.
      </div>
    );
  }

  // Compute SVG dimensions
  let maxX = 0;
  let maxY = 0;
  for (const pos of positioned.values()) {
    if (pos.x > maxX) maxX = pos.x;
    if (pos.y > maxY) maxY = pos.y;
  }
  const svgWidth = Math.max(maxX + PADDING_X + RECT_W, 600);
  const svgHeight = Math.max(maxY + 80, 500);

  const handleNodeClick = (node: NetworkNode) => {
    if (selectedId === node.id) {
      setSelectedId(null);
    } else {
      setSelectedId(node.id);
    }
    if (node.type === "vessel" && node.vessel_id != null) {
      navigate(`/vessels/${node.vessel_id}`);
    }
  };

  return (
    <div style={{ position: "relative", overflow: "auto" }}>
      <svg
        data-testid="network-graph-svg"
        width={svgWidth}
        height={svgHeight}
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        style={{ display: "block", overflow: "visible" }}
      >
        {/* Layer labels */}
        {[
          { layer: 0, label: "Root Companies" },
          { layer: 1, label: "Intermediaries" },
          { layer: 2, label: "Leaf Owners" },
          { layer: 3, label: "Vessels" },
        ].map(({ layer, label }) => (
          <text
            key={`label-${layer}`}
            x={12}
            y={(LAYER_Y[layer] ?? 60) + 4}
            fontSize={10}
            fill="var(--text-dim, #64748b)"
            fontWeight={500}
          >
            {label}
          </text>
        ))}

        {/* Edges */}
        {edges.map((edge, i) => {
          const src = positioned.get(edge.source);
          const tgt = positioned.get(edge.target);
          if (!src || !tgt) return null;

          const isHighlighted =
            highlightedIds.has(edge.source) && highlightedIds.has(edge.target);
          const isDashed = edge.relationship === "cluster_related";

          return (
            <line
              key={`edge-${i}`}
              data-testid="network-edge"
              x1={src.x + RECT_W / 2}
              y1={src.y + RECT_H / 2}
              x2={tgt.x + RECT_W / 2}
              y2={tgt.y + (tgt.y > src.y ? 0 : RECT_H)}
              stroke={isHighlighted ? "#ffffff" : "var(--text-dim, #475569)"}
              strokeWidth={isHighlighted ? 2 : 1}
              strokeOpacity={isHighlighted ? 0.9 : 0.4}
              strokeDasharray={isDashed ? "4 3" : undefined}
              markerEnd={isDashed ? undefined : "url(#arrowhead)"}
            />
          );
        })}

        {/* Arrow marker */}
        <defs>
          <marker
            id="arrowhead"
            markerWidth="8"
            markerHeight="6"
            refX="8"
            refY="3"
            orient="auto"
          >
            <polygon
              points="0 0, 8 3, 0 6"
              fill="var(--text-dim, #475569)"
            />
          </marker>
        </defs>

        {/* Nodes */}
        {nodes.map((node) => {
          const pos = positioned.get(node.id);
          if (!pos) return null;

          const color = getNodeColor(node);
          const isHighlighted = highlightedIds.has(node.id);
          const stroke = getNodeStroke(node, isHighlighted);

          if (node.type === "vessel") {
            // Circle for vessels
            const cx = pos.x + RECT_W / 2;
            const cy = pos.y + RECT_H / 2;
            return (
              <g
                key={node.id}
                data-testid="network-node"
                data-node-type="vessel"
                style={{ cursor: "pointer" }}
                onClick={() => handleNodeClick(node)}
                onMouseEnter={(e) => setHoveredNode({ node, x: e.clientX, y: e.clientY })}
                onMouseLeave={() => setHoveredNode(null)}
              >
                <circle
                  cx={cx}
                  cy={cy}
                  r={CIRCLE_R}
                  fill={color}
                  fillOpacity={0.15}
                  stroke={stroke}
                  strokeWidth={isHighlighted ? 3 : 2}
                />
                <text
                  x={cx}
                  y={cy}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize={9}
                  fontWeight={600}
                  fill="var(--text-body, #e2e8f0)"
                >
                  {node.label.length > 12
                    ? node.label.slice(0, 10) + ".."
                    : node.label}
                </text>
              </g>
            );
          }

          // Rectangle for companies
          return (
            <g
              key={node.id}
              data-testid="network-node"
              data-node-type="company"
              style={{ cursor: "pointer" }}
              onClick={() => handleNodeClick(node)}
              onMouseEnter={(e) => setHoveredNode({ node, x: e.clientX, y: e.clientY })}
              onMouseLeave={() => setHoveredNode(null)}
            >
              <rect
                x={pos.x}
                y={pos.y}
                width={RECT_W}
                height={RECT_H}
                rx={4}
                fill={color}
                fillOpacity={0.1}
                stroke={stroke}
                strokeWidth={isHighlighted ? 3 : 2}
              />
              <text
                x={pos.x + RECT_W / 2}
                y={pos.y + RECT_H / 2}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={10}
                fontWeight={500}
                fill="var(--text-body, #e2e8f0)"
              >
                {node.label.length > 14
                  ? node.label.slice(0, 12) + ".."
                  : node.label}
              </text>
            </g>
          );
        })}
      </svg>

      {/* Tooltip */}
      {hoveredNode && (
        <NetworkNodeTooltip
          node={hoveredNode.node}
          x={hoveredNode.x}
          y={hoveredNode.y}
        />
      )}
    </div>
  );
}
