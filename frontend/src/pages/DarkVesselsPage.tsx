import { Link } from 'react-router-dom'
import { useDarkVessels } from '../hooks/useDarkVessels'
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

function formatCoord(val: number | null | undefined, decimals = 4): string {
  if (val == null) return '-'
  return val.toFixed(decimals)
}

function formatConfidence(val: number | null | undefined): string {
  if (val == null) return '-'
  return `${(val * 100).toFixed(1)}%`
}

export function DarkVesselsPage() {
  const { data: detections, isLoading, error } = useDarkVessels()

  return (
    <div style={{ maxWidth: 1100 }}>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        Dark Vessel Detections
      </h2>

      <Card>
        {isLoading && <Spinner text="Loading dark vessel detections..." />}
        {error && (
          <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem' }}>
            Failed to load dark vessel detections
          </p>
        )}

        {detections && detections.length === 0 && (
          <EmptyState
            title="No dark vessel detections"
            description="Satellite-detected vessels with no matching AIS will appear here once imported"
          />
        )}

        {detections && detections.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={headStyle}>ID</th>
                  <th style={headStyle}>Detection Time</th>
                  <th style={headStyle}>Lat</th>
                  <th style={headStyle}>Lon</th>
                  <th style={headStyle}>Matched Vessel</th>
                  <th style={headStyle}>AIS Match</th>
                  <th style={headStyle}>Corridor ID</th>
                  <th style={headStyle}>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {detections.map((det) => (
                  <tr
                    key={det.detection_id}
                    style={{ borderBottom: '1px solid var(--border)' }}
                  >
                    <td style={{ ...cellStyle, fontFamily: 'monospace' }}>
                      {det.detection_id}
                    </td>
                    <td style={{ ...cellStyle, whiteSpace: 'nowrap' }}>
                      {formatTimestamp(det.detection_time_utc)}
                    </td>
                    <td style={{ ...cellStyle, fontFamily: 'monospace', textAlign: 'right' }}>
                      {formatCoord(det.detection_lat)}
                    </td>
                    <td style={{ ...cellStyle, fontFamily: 'monospace', textAlign: 'right' }}>
                      {formatCoord(det.detection_lon)}
                    </td>
                    <td style={cellStyle}>
                      {det.matched_vessel_id != null ? (
                        <Link
                          to={`/vessels/${det.matched_vessel_id}`}
                          style={{ color: 'var(--accent)', textDecoration: 'none' }}
                        >
                          {det.matched_vessel_id}
                        </Link>
                      ) : (
                        <span style={{ color: 'var(--text-dim)' }}>-</span>
                      )}
                    </td>
                    <td style={cellStyle}>
                      {det.ais_match_result ?? '-'}
                    </td>
                    <td style={{ ...cellStyle, fontFamily: 'monospace' }}>
                      {det.corridor_id != null ? det.corridor_id : '-'}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'right', fontWeight: 600 }}>
                      {formatConfidence(det.model_confidence)}
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
