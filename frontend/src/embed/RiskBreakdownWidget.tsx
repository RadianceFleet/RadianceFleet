import type { WidgetTheme } from './widgetTheme'

interface Signal {
  signal: string
  value: number
}

interface RiskData {
  vessel_id: number
  risk_score: number | null
  risk_tier: string
  top_signals: Signal[]
}

interface Props {
  data: RiskData
  theme: WidgetTheme
}

function formatSignalName(name: string): string {
  return name
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export default function RiskBreakdownWidget({ data, theme }: Props) {
  const tierColor = theme.tierColors[data.risk_tier] || theme.tierColors.unknown
  const maxValue = data.top_signals.length > 0
    ? Math.max(...data.top_signals.map((s) => s.value))
    : 1

  return (
    <div style={{ fontFamily: 'system-ui, sans-serif' }}>
      {/* Overall score */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
        <div
          style={{
            width: 56,
            height: 56,
            borderRadius: '50%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: tierColor,
            color: '#fff',
            fontWeight: 700,
            fontSize: '1.3em',
          }}
        >
          {data.risk_score ?? '?'}
        </div>
        <div>
          <div style={{ fontWeight: 700, color: theme.text, fontSize: '1.1em' }}>
            Risk: {data.risk_tier.toUpperCase()}
          </div>
          <div style={{ color: theme.textSecondary, fontSize: '0.85em' }}>
            {data.top_signals.length} contributing signal{data.top_signals.length !== 1 ? 's' : ''}
          </div>
        </div>
      </div>

      {/* Signal bars */}
      {data.top_signals.map((signal) => (
        <div key={signal.signal} style={{ marginBottom: 8 }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: '0.85em',
              marginBottom: 2,
            }}
          >
            <span style={{ color: theme.text }}>{formatSignalName(signal.signal)}</span>
            <span style={{ color: theme.textSecondary }}>+{signal.value}</span>
          </div>
          <div
            style={{
              height: 6,
              borderRadius: 3,
              background: theme.border,
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                height: '100%',
                width: `${Math.round((signal.value / maxValue) * 100)}%`,
                background: theme.accent,
                borderRadius: 3,
                transition: 'width 0.3s',
              }}
            />
          </div>
        </div>
      ))}

      {data.top_signals.length === 0 && (
        <div style={{ color: theme.textSecondary, fontSize: '0.9em' }}>
          No risk signals available.
        </div>
      )}
    </div>
  )
}
