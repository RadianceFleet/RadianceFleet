import { useNavigate } from "react-router-dom";
import type { MergeChainNode, MergeChainEdge } from "../types/api";

interface MergeChainGraphProps {
  nodes: MergeChainNode[];
  edges: MergeChainEdge[];
  confidenceBand: string;
}

const bandColors: Record<string, string> = {
  HIGH: "#22c55e",
  MEDIUM: "#f59e0b",
  LOW: "#ef4444",
};

export function MergeChainGraph({ nodes, edges, confidenceBand }: MergeChainGraphProps) {
  const navigate = useNavigate();

  if (nodes.length === 0) return null;

  const nodeRadius = 20;
  const padding = 60;
  const spacingX = 120;
  const svgWidth = Math.max(padding * 2 + (nodes.length - 1) * spacingX, 200);
  const svgHeight = 140;

  const nodeColor = bandColors[confidenceBand] ?? bandColors.LOW;

  // Position nodes evenly along horizontal axis
  const nodePositions = nodes.map((node, i) => ({
    ...node,
    x: padding + i * spacingX,
    y: svgHeight / 2 - 10,
  }));

  const posMap = new Map(nodePositions.map((n) => [n.vessel_id, n]));

  // Build index map for arc detection
  const indexMap = new Map(nodes.map((n, i) => [n.vessel_id, i]));

  return (
    <svg
      width={svgWidth}
      height={svgHeight}
      viewBox={`0 0 ${svgWidth} ${svgHeight}`}
      style={{ display: "block", overflow: "visible" }}
    >
      {/* Edges */}
      {edges.map((edge, i) => {
        const src = posMap.get(edge.source_id);
        const tgt = posMap.get(edge.target_id);
        if (!src || !tgt) return null;

        const srcIdx = indexMap.get(edge.source_id) ?? 0;
        const tgtIdx = indexMap.get(edge.target_id) ?? 0;
        const isAdjacent = Math.abs(srcIdx - tgtIdx) === 1;

        if (isAdjacent) {
          return (
            <line
              key={`edge-${i}`}
              x1={src.x}
              y1={src.y}
              x2={tgt.x}
              y2={tgt.y}
              stroke="var(--text-dim, #888)"
              strokeWidth={2}
              strokeOpacity={0.6}
            />
          );
        }

        // Non-adjacent: use a quadratic bezier arc above
        const midX = (src.x + tgt.x) / 2;
        const arcHeight = Math.min(40, Math.abs(tgtIdx - srcIdx) * 20);
        const midY = Math.min(src.y, tgt.y) - arcHeight;
        return (
          <path
            key={`edge-${i}`}
            d={`M ${src.x} ${src.y} Q ${midX} ${midY} ${tgt.x} ${tgt.y}`}
            fill="none"
            stroke="var(--text-dim, #888)"
            strokeWidth={2}
            strokeOpacity={0.4}
            strokeDasharray="4 3"
          />
        );
      })}

      {/* Nodes */}
      {nodePositions.map((node) => (
        <g
          key={node.vessel_id}
          style={{ cursor: "pointer" }}
          onClick={() => navigate(`/vessels/${node.vessel_id}`)}
        >
          <circle
            cx={node.x}
            cy={node.y}
            r={nodeRadius}
            fill={nodeColor}
            fillOpacity={0.15}
            stroke={nodeColor}
            strokeWidth={2}
          />
          <text
            x={node.x}
            y={node.y}
            textAnchor="middle"
            dominantBaseline="central"
            fontSize={9}
            fontWeight={600}
            fill="var(--text-body, #ccc)"
          >
            {node.mmsi ? node.mmsi.slice(-4) : "?"}
          </text>
          <text
            x={node.x}
            y={node.y + nodeRadius + 14}
            textAnchor="middle"
            fontSize={10}
            fill="var(--text-muted, #aaa)"
          >
            {node.name ?? "Unknown"}
          </text>
        </g>
      ))}
    </svg>
  );
}
