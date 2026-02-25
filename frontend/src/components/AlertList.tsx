import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import type { AlertSummary } from '../types/api'

const API_BASE = '/api/v1'

export interface Filters {
  min_score: string
  status: string
  vessel_name: string
}

function ScoreBadge({ score }: { score: number }) {
  const bg =
    score >= 76 ? '#dc2626' :
    score >= 51 ? '#ea580c' :
    score >= 21 ? '#d97706' : '#16a34a'
  return (
    <span style={{ background: bg, color: '#fff', borderRadius: 4, padding: '2px 8px', fontWeight: 700, fontSize: 13 }}>
      {score}
    </span>
  )
}

export function AlertList({ filters }: { filters: Filters }) {
  const params = new URLSearchParams()
  if (filters.min_score) params.set('min_score', filters.min_score)
  if (filters.status) params.set('status', filters.status)
  if (filters.vessel_name) params.set('vessel_name', filters.vessel_name)

  const { data: alerts, isLoading, error } = useQuery<AlertSummary[]>({
    queryKey: ['alerts', filters],
    queryFn: () => fetch(`${API_BASE}/alerts?${params}`).then(r => r.json()),
  })

  if (isLoading) return <p>Loading alerts…</p>
  if (error) return <p style={{ color: 'red' }}>Error loading alerts.</p>
  if (!alerts?.length) return <p style={{ color: '#64748b' }}>No alerts. Run: <code>make detect</code></p>

  const th: React.CSSProperties = { padding: '8px 12px', textAlign: 'left', fontWeight: 600, color: '#94a3b8' }
  const td: React.CSSProperties = { padding: '8px 12px' }

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
      <thead>
        <tr style={{ background: '#1e293b' }}>
          <th style={th}>ID</th>
          <th style={th}>Score</th>
          <th style={th}>Gap Start (UTC)</th>
          <th style={th}>Duration</th>
          <th style={th}>Status</th>
          <th style={th}>Flags</th>
        </tr>
      </thead>
      <tbody>
        {alerts.map(a => (
          <tr key={a.gap_event_id} style={{ borderBottom: '1px solid #1e293b' }}>
            <td style={td}>
              <Link to={`/alerts/${a.gap_event_id}`} style={{ color: '#60a5fa' }}>#{a.gap_event_id}</Link>
            </td>
            <td style={td}><ScoreBadge score={a.risk_score} /></td>
            <td style={td}>{a.gap_start_utc.slice(0, 16).replace('T', ' ')}</td>
            <td style={td}>{(a.duration_minutes / 60).toFixed(1)}h</td>
            <td style={td}>{a.status.replace(/_/g, ' ')}</td>
            <td style={td}>
              {a.impossible_speed_flag && <span title="Impossible speed">⚠️</span>}
              {a.in_dark_zone && <span title="Dark zone">🌑</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
