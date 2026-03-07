import { Link } from 'react-router-dom'
import type { StsChain } from '../../hooks/useStsChains'

const NODE_R = 22
const NODE_SPACING = 140
const ROW_HEIGHT = 80
const PADDING = 30

function ChainRow({ chain, y }: { chain: StsChain; y: number }) {
  const ids = chain.chain_vessel_ids
  if (ids.length === 0) return null

  const nodes = ids.map((vid, i) => ({
    vid,
    name: chain.vessel_names[vid] ?? `#${vid}`,
    cx: PADDING + NODE_R + i * NODE_SPACING,
    cy: y + ROW_HEIGHT / 2,
  }))

  return (
    <g>
      {/* arrows between nodes */}
      {nodes.slice(0, -1).map((from, i) => {
        const to = nodes[i + 1]
        return (
          <line
            key={`arrow-${from.vid}-${to.vid}`}
            x1={from.cx + NODE_R + 2}
            y1={from.cy}
            x2={to.cx - NODE_R - 2}
            y2={to.cy}
            stroke="var(--text-muted)"
            strokeWidth={1.5}
            markerEnd="url(#arrowhead)"
          />
        )
      })}

      {/* vessel nodes */}
      {nodes.map((n) => (
        <g key={n.vid}>
          <circle cx={n.cx} cy={n.cy} r={NODE_R} fill="var(--bg-card)" stroke="var(--accent)" strokeWidth={2} />
          <foreignObject x={n.cx - NODE_R} y={n.cy - NODE_R} width={NODE_R * 2} height={NODE_R * 2}>
            <div style={{
              width: '100%', height: '100%',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Link
                to={`/vessels/${n.vid}`}
                style={{
                  fontSize: 9, color: 'var(--accent)', textDecoration: 'none',
                  textAlign: 'center', lineHeight: 1.1, wordBreak: 'break-all',
                  maxWidth: NODE_R * 2 - 4,
                }}
                title={n.name}
              >
                {n.name.length > 10 ? n.name.slice(0, 9) + '\u2026' : n.name}
              </Link>
            </div>
          </foreignObject>
        </g>
      ))}

      {/* risk score label */}
      <text
        x={PADDING + NODE_R + (ids.length - 1) * NODE_SPACING + NODE_R + 14}
        y={y + ROW_HEIGHT / 2 + 4}
        fontSize={11}
        fill="var(--text-muted)"
      >
        Risk: {chain.risk_score_component}
      </text>
    </g>
  )
}

interface Props {
  chains: StsChain[]
}

export function StsChainDiagram({ chains }: Props) {
  if (chains.length === 0) return null

  const maxChainLen = Math.max(...chains.map(c => c.chain_vessel_ids.length))
  const svgWidth = PADDING * 2 + maxChainLen * NODE_SPACING + 80
  const svgHeight = chains.length * ROW_HEIGHT + PADDING

  return (
    <svg width="100%" viewBox={`0 0 ${svgWidth} ${svgHeight}`} style={{ maxWidth: svgWidth }}>
      <defs>
        <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
          <polygon points="0 0, 8 3, 0 6" fill="var(--text-muted)" />
        </marker>
      </defs>
      {chains.map((chain, i) => (
        <ChainRow key={chain.alert_id} chain={chain} y={i * ROW_HEIGHT + PADDING / 2} />
      ))}
    </svg>
  )
}
