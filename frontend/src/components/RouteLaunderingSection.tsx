import { useRouteLaundering } from '../hooks/useRouteLaundering'
import { Card } from './ui/Card'
import { Spinner } from './ui/Spinner'
import { EmptyState } from './ui/EmptyState'
import { ScoreBadge } from './ui/ScoreBadge'
import { sectionHead, thStyle, tdStyle, tableStyle, theadRow, tbodyRow } from '../styles/tables'

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return '--'
  return ts.slice(0, 19).replace('T', ' ') + ' UTC'
}

export function RouteLaunderingSection({ vesselId }: { vesselId: string | number }) {
  const { data, isLoading, error } = useRouteLaundering(vesselId)

  if (isLoading) return <Spinner text="Loading route laundering data..." />
  if (error) {
    return (
      <Card style={{ marginBottom: 16 }}>
        <h3 style={sectionHead}>Route Laundering Detections</h3>
        <p style={{ color: 'var(--score-critical)', fontSize: 13 }}>
          Failed to load route laundering data.
        </p>
      </Card>
    )
  }

  const items = data?.items ?? []

  return (
    <Card style={{ marginBottom: 16 }}>
      <h3 style={sectionHead}>Route Laundering Detections</h3>
      {items.length === 0 ? (
        <EmptyState title="No route laundering anomalies detected" />
      ) : (
        <table style={tableStyle}>
          <thead>
            <tr style={theadRow}>
              <th style={thStyle}>ID</th>
              <th style={thStyle}>Start Time (UTC)</th>
              <th style={thStyle}>End Time (UTC)</th>
              <th style={thStyle}>Origin</th>
              <th style={thStyle}>Intermediate</th>
              <th style={thStyle}>Destination</th>
              <th style={thStyle}>Score</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item, i) => {
              const evidence = item.evidence_json ?? {}
              const origin = (evidence.origin as string) ?? '--'
              const intermediate = (evidence.intermediate as string) ?? '--'
              const destination = (evidence.destination as string) ?? '--'
              return (
                <tr key={item.anomaly_id ?? i} style={tbodyRow}>
                  <td style={tdStyle}>#{item.anomaly_id}</td>
                  <td style={tdStyle}>{formatTimestamp(item.start_time_utc)}</td>
                  <td style={tdStyle}>{formatTimestamp(item.end_time_utc)}</td>
                  <td style={tdStyle}>{origin}</td>
                  <td style={tdStyle}>
                    {intermediate !== '--' ? (
                      <span style={{
                        display: 'inline-block',
                        padding: '2px 6px',
                        borderRadius: 'var(--radius)',
                        fontSize: 11,
                        background: 'var(--bg-base)',
                        border: '1px solid var(--border)',
                        color: 'var(--warning)',
                      }}>
                        {intermediate}
                      </span>
                    ) : '--'}
                  </td>
                  <td style={tdStyle}>{destination}</td>
                  <td style={tdStyle}>
                    <ScoreBadge score={item.risk_score_component} size="sm" />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Card>
  )
}
