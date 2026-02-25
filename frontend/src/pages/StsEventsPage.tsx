import { Link } from 'react-router-dom'
import { useStsEvents } from '../hooks/useStsEvents'
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

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return '-'
  return ts.slice(0, 16).replace('T', ' ')
}

export function StsEventsPage() {
  const { data: events, isLoading, error } = useStsEvents()

  return (
    <div style={{ maxWidth: 1100 }}>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        STS Transfer Events
      </h2>

      <Card>
        {isLoading && <Spinner text="Loading STS events..." />}
        {error && (
          <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem' }}>
            Failed to load STS events
          </p>
        )}

        {events && events.length === 0 && (
          <EmptyState
            title="No STS events detected"
            description="Ship-to-ship transfer events will appear here once detected"
          />
        )}

        {events && events.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={headStyle}>STS ID</th>
                  <th style={headStyle}>Vessel 1 ID</th>
                  <th style={headStyle}>Vessel 2 ID</th>
                  <th style={headStyle}>Detection Type</th>
                  <th style={headStyle}>Start Time</th>
                  <th style={headStyle}>Duration (minutes)</th>
                  <th style={headStyle}>Proximity (meters)</th>
                  <th style={headStyle}>Corridor ID</th>
                  <th style={headStyle}>Risk Score</th>
                </tr>
              </thead>
              <tbody>
                {events.map((evt) => (
                  <tr
                    key={evt.sts_id as number}
                    style={{ borderBottom: '1px solid var(--border)' }}
                  >
                    <td style={{ ...cellStyle, fontFamily: 'monospace' }}>
                      {evt.sts_id as number}
                    </td>
                    <td style={cellStyle}>
                      <Link
                        to={`/vessels/${evt.vessel_1_id}`}
                        style={{ color: 'var(--accent)', textDecoration: 'none' }}
                      >
                        {evt.vessel_1_id as number}
                      </Link>
                    </td>
                    <td style={cellStyle}>
                      <Link
                        to={`/vessels/${evt.vessel_2_id}`}
                        style={{ color: 'var(--accent)', textDecoration: 'none' }}
                      >
                        {evt.vessel_2_id as number}
                      </Link>
                    </td>
                    <td style={cellStyle}>
                      {(evt.detection_type as string) ?? '-'}
                    </td>
                    <td style={{ ...cellStyle, whiteSpace: 'nowrap' }}>
                      {formatTimestamp(evt.start_time_utc as string | null)}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'right' }}>
                      {evt.duration_minutes != null
                        ? (evt.duration_minutes as number)
                        : '-'}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'right' }}>
                      {evt.mean_proximity_meters != null
                        ? `${Math.round(evt.mean_proximity_meters as number)}m`
                        : '-'}
                    </td>
                    <td style={{ ...cellStyle, fontFamily: 'monospace' }}>
                      {evt.corridor_id != null ? (evt.corridor_id as number) : '-'}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'right', fontWeight: 600 }}>
                      {evt.risk_score_component != null
                        ? (evt.risk_score_component as number)
                        : '-'}
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
