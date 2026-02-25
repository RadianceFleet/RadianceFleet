import { useState } from 'react'

interface Props {
  breakdown: Record<string, unknown>
}

const RISK_COLOR = '#ef4444'
const LEGIT_COLOR = '#22c55e'

export function ScoreBreakdown({ breakdown }: Props) {
  const [showRaw, setShowRaw] = useState(false)

  const entries = Object.entries(breakdown).filter(
    ([, v]) => typeof v === 'number'
  ) as [string, number][]

  const positiveSignals = entries.filter(([k, v]) => !k.startsWith('_') && v > 0)
  const negativeSignals = entries.filter(([k, v]) => !k.startsWith('_') && v < 0)
  const metadata = entries.filter(([k]) => k.startsWith('_'))

  const maxAbs = Math.max(
    ...positiveSignals.map(([, v]) => v),
    ...negativeSignals.map(([, v]) => Math.abs(v)),
    1
  )

  const formatLabel = (key: string) =>
    key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())

  return (
    <div style={{ fontSize: 12 }}>
      {positiveSignals.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ color: 'var(--text-muted)', fontWeight: 600, marginBottom: 4 }}>Risk Signals</div>
          {positiveSignals
            .sort((a, b) => b[1] - a[1])
            .map(([key, val]) => (
              <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                <div style={{ width: 160, color: 'var(--text-body)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={key}>
                  {formatLabel(key)}
                </div>
                <div style={{ flex: 1, background: 'var(--bg-base)', borderRadius: 2, height: 14 }}>
                  <div style={{
                    width: `${(val / maxAbs) * 100}%`,
                    height: '100%',
                    background: RISK_COLOR,
                    borderRadius: 2,
                    opacity: 0.7,
                  }} />
                </div>
                <div style={{ width: 36, textAlign: 'right', fontFamily: 'monospace', color: RISK_COLOR }}>
                  +{val}
                </div>
              </div>
            ))}
        </div>
      )}

      {negativeSignals.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ color: 'var(--text-muted)', fontWeight: 600, marginBottom: 4 }}>Legitimacy Signals</div>
          {negativeSignals
            .sort((a, b) => a[1] - b[1])
            .map(([key, val]) => (
              <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
                <div style={{ width: 160, color: 'var(--text-body)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={key}>
                  {formatLabel(key)}
                </div>
                <div style={{ flex: 1, background: 'var(--bg-base)', borderRadius: 2, height: 14 }}>
                  <div style={{
                    width: `${(Math.abs(val) / maxAbs) * 100}%`,
                    height: '100%',
                    background: LEGIT_COLOR,
                    borderRadius: 2,
                    opacity: 0.7,
                  }} />
                </div>
                <div style={{ width: 36, textAlign: 'right', fontFamily: 'monospace', color: LEGIT_COLOR }}>
                  {val}
                </div>
              </div>
            ))}
        </div>
      )}

      {metadata.length > 0 && (
        <div style={{ color: 'var(--text-dim)', fontSize: 11, marginBottom: 8 }}>
          {metadata.map(([k, v]) => (
            <span key={k} style={{ marginRight: 12 }}>
              {formatLabel(k.slice(1))}: <b>{typeof v === 'number' ? v.toFixed(2) : String(v)}</b>
            </span>
          ))}
        </div>
      )}

      <button
        onClick={() => setShowRaw(p => !p)}
        style={{
          background: 'none', border: '1px solid var(--border)', borderRadius: 'var(--radius)',
          color: 'var(--text-dim)', fontSize: 11, padding: '2px 8px', cursor: 'pointer',
        }}
      >
        {showRaw ? 'Hide' : 'Show'} raw JSON
      </button>
      {showRaw && (
        <pre style={{ fontSize: 11, color: 'var(--text-body)', overflowX: 'auto', maxHeight: 200, marginTop: 6 }}>
          {JSON.stringify(breakdown, null, 2)}
        </pre>
      )}
    </div>
  )
}
