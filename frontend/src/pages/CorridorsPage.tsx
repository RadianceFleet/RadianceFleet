import { Link } from 'react-router-dom'
import { useCorridors } from '../hooks/useCorridors'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import { EmptyState } from '../components/ui/EmptyState'

const cellStyle: React.CSSProperties = { padding: '0.5rem 0.75rem', fontSize: '0.8125rem' }
const headStyle: React.CSSProperties = {
  ...cellStyle,
  fontWeight: 600,
  color: 'var(--text-muted)',
  textAlign: 'left' as const,
  borderBottom: '1px solid var(--border)',
}

function formatType(raw: string): string {
  return raw.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

export function CorridorsPage() {
  const { data: corridors, isLoading, error } = useCorridors()

  return (
    <div style={{ maxWidth: 1000 }}>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        Corridors &amp; Zones
      </h2>

      <Card>
        {isLoading && <Spinner text="Loading corridors..." />}
        {error && (
          <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem' }}>
            Failed to load corridors
          </p>
        )}

        {corridors && corridors.length === 0 && (
          <EmptyState
            title="No corridors found"
            description="Import corridor data via the CLI to get started"
          />
        )}

        {corridors && corridors.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={headStyle}>Name</th>
                  <th style={headStyle}>Type</th>
                  <th style={{ ...headStyle, textAlign: 'right' }}>Risk Weight</th>
                  <th style={headStyle}>Jamming Zone</th>
                  <th style={{ ...headStyle, textAlign: 'right' }}>Alerts (7d)</th>
                  <th style={{ ...headStyle, textAlign: 'right' }}>Alerts (30d)</th>
                  <th style={{ ...headStyle, textAlign: 'right' }}>Avg Score</th>
                </tr>
              </thead>
              <tbody>
                {corridors.map(c => (
                  <tr
                    key={String(c.corridor_id)}
                    style={{ borderBottom: '1px solid var(--border)' }}
                  >
                    <td style={cellStyle}>
                      <Link
                        to={`/corridors/${c.corridor_id}`}
                        style={{ color: 'var(--accent-primary)', textDecoration: 'none' }}
                      >
                        {String(c.name)}
                      </Link>
                    </td>
                    <td style={{ ...cellStyle, color: 'var(--text-dim)' }}>
                      {formatType(String(c.corridor_type ?? ''))}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'right', fontFamily: 'monospace' }}>
                      {c.risk_weight != null ? Number(c.risk_weight).toFixed(1) : '-'}
                    </td>
                    <td style={cellStyle}>
                      <span
                        style={{
                          display: 'inline-block',
                          padding: '0.125rem 0.5rem',
                          borderRadius: 'var(--radius)',
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          background: c.is_jamming_zone
                            ? 'rgba(239, 68, 68, 0.15)'
                            : 'rgba(255, 255, 255, 0.06)',
                          color: c.is_jamming_zone
                            ? 'var(--score-critical)'
                            : 'var(--text-dim)',
                          border: c.is_jamming_zone
                            ? '1px solid rgba(239, 68, 68, 0.3)'
                            : '1px solid var(--border)',
                        }}
                      >
                        {c.is_jamming_zone ? 'Yes' : 'No'}
                      </span>
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'right', fontFamily: 'monospace' }}>
                      {Number(c.alert_count_7d ?? 0)}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'right', fontFamily: 'monospace' }}>
                      {Number(c.alert_count_30d ?? 0)}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'right', fontFamily: 'monospace' }}>
                      {c.avg_risk_score != null ? Number(c.avg_risk_score).toFixed(1) : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
