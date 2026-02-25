import { useParams, Link, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import type { AlertDetail as AlertDetailType, AlertStatus } from '../types/api'
import { AlertMap } from './AlertMap'

const API_BASE = '/api/v1'
const STATUSES: AlertStatus[] = ['new', 'under_review', 'needs_satellite_check', 'documented', 'dismissed']

const card: React.CSSProperties = { background: '#1e293b', borderRadius: 8, padding: 16, marginBottom: 16 }
const sectionHead: React.CSSProperties = { margin: '0 0 12px', fontSize: 14, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 1 }
const labelCell: React.CSSProperties = { color: '#64748b', width: 180, fontSize: 13, paddingRight: 12, paddingBottom: 8, verticalAlign: 'top' }
const valueCell: React.CSSProperties = { fontSize: 13, paddingBottom: 8 }
const btn: React.CSSProperties = { border: '1px solid #334155', borderRadius: 4, padding: '6px 14px', cursor: 'pointer', fontSize: 13 }

export function AlertDetail() {
  const { id } = useParams<{ id: string }>()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [notes, setNotes] = useState('')
  const [saved, setSaved] = useState(false)

  const { data: alert, isLoading, error } = useQuery<AlertDetailType>({
    queryKey: ['alert', id],
    queryFn: () => fetch(`${API_BASE}/alerts/${id}`).then(r => {
      if (!r.ok) throw new Error('Not found')
      return r.json()
    }),
  })

  useEffect(() => {
    if (alert?.analyst_notes) setNotes(alert.analyst_notes)
  }, [alert?.analyst_notes])

  const statusMutation = useMutation({
    mutationFn: (status: AlertStatus) =>
      fetch(`${API_BASE}/alerts/${id}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      }).then(r => r.json()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alert', id] }),
  })

  const notesMutation = useMutation({
    mutationFn: (text: string) =>
      fetch(`${API_BASE}/alerts/${id}/notes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notes: text }),
      }).then(r => r.json()),
    onSuccess: () => {
      setSaved(true)
      qc.invalidateQueries({ queryKey: ['alert', id] })
    },
  })

  const handleExport = async (fmt: 'md' | 'json') => {
    const res = await fetch(`${API_BASE}/alerts/${id}/export?format=${fmt}`, { method: 'POST' })
    const data = await res.json()
    const content = data.content ?? JSON.stringify(data, null, 2)
    const blob = new Blob([content], { type: fmt === 'json' ? 'application/json' : 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `alert_${id}.${fmt}`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (isLoading) return <p>Loading…</p>
  if (error || !alert) {
    return (
      <p style={{ color: '#ef4444' }}>
        Alert not found.{' '}
        <Link to="/" style={{ color: '#60a5fa' }}>← Back</Link>
      </p>
    )
  }

  const scoreColor = alert.risk_score >= 76 ? '#dc2626'
    : alert.risk_score >= 51 ? '#ea580c'
    : alert.risk_score >= 21 ? '#d97706'
    : '#16a34a'

  return (
    <div style={{ maxWidth: 860 }}>
      <Link to="/" style={{ color: '#60a5fa', fontSize: 13 }}>← All alerts</Link>
      <h2 style={{ margin: '12px 0 4px', fontSize: 18 }}>Alert #{alert.gap_event_id}</h2>
      <p style={{ color: '#64748b', margin: '0 0 20px', fontSize: 13 }}>
        {alert.vessel_name ?? 'Unknown vessel'}
        {' · MMSI '}{alert.vessel_mmsi ?? '?'}
        {' · '}{alert.vessel_flag ?? '?'}
        {alert.vessel_deadweight != null && ` · ${alert.vessel_deadweight.toLocaleString()} DWT`}
        {alert.corridor_name && ` · ${alert.corridor_name}`}
      </p>

      <AlertMap
        lastPoint={alert.last_point}
        firstPointAfter={alert.first_point_after}
        envelope={alert.movement_envelope}
      />

      <section style={card}>
        <h3 style={sectionHead}>Gap Details</h3>
        <table><tbody>
          <tr>
            <td style={labelCell}>Start</td>
            <td style={valueCell}>{alert.gap_start_utc.slice(0, 19).replace('T', ' ')} UTC</td>
          </tr>
          <tr>
            <td style={labelCell}>End</td>
            <td style={valueCell}>{alert.gap_end_utc.slice(0, 19).replace('T', ' ')} UTC</td>
          </tr>
          <tr>
            <td style={labelCell}>Duration</td>
            <td style={valueCell}>{(alert.duration_minutes / 60).toFixed(1)} h</td>
          </tr>
          <tr>
            <td style={labelCell}>In dark zone</td>
            <td style={valueCell}>{alert.in_dark_zone ? '🌑 Yes (GPS jamming zone)' : 'No'}</td>
          </tr>
          {alert.velocity_plausibility_ratio != null && (
            <tr>
              <td style={labelCell}>Velocity ratio</td>
              <td style={valueCell}>
                {alert.velocity_plausibility_ratio.toFixed(3)}
                {alert.impossible_speed_flag && ' ⚠️ Physically impossible'}
              </td>
            </tr>
          )}
          {alert.max_plausible_distance_nm != null && (
            <tr>
              <td style={labelCell}>Max plausible dist.</td>
              <td style={valueCell}>{alert.max_plausible_distance_nm.toFixed(0)} nm</td>
            </tr>
          )}
        </tbody></table>
      </section>

      <section style={card}>
        <h3 style={sectionHead}>
          Risk Score:{' '}
          <span style={{ color: scoreColor, fontSize: 22 }}>{alert.risk_score}</span>
        </h3>
        {alert.risk_breakdown_json && Object.keys(alert.risk_breakdown_json).length > 0 && (
          <details>
            <summary style={{ cursor: 'pointer', color: '#94a3b8', fontSize: 12 }}>Score breakdown ▾</summary>
            <pre style={{ fontSize: 11, color: '#cbd5e1', overflowX: 'auto', maxHeight: 260, marginTop: 8 }}>
              {JSON.stringify(alert.risk_breakdown_json, null, 2)}
            </pre>
          </details>
        )}
      </section>

      {alert.satellite_check && (
        <section style={card}>
          <h3 style={sectionHead}>Satellite Check</h3>
          <p style={{ fontSize: 13, margin: '0 0 8px' }}>
            Status: <b>{alert.satellite_check.review_status.replace(/_/g, ' ')}</b>
          </p>
          {alert.satellite_check.copernicus_url && (
            <a
              href={alert.satellite_check.copernicus_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: '#60a5fa', fontSize: 13 }}
            >
              Open Copernicus Browser ↗
            </a>
          )}
        </section>
      )}

      <section style={card}>
        <h3 style={sectionHead}>Analyst Workflow</h3>
        <div style={{ marginBottom: 14 }}>
          <label style={{ fontSize: 12, color: '#64748b' }}>Status</label><br />
          <select
            value={alert.status}
            onChange={e => statusMutation.mutate(e.target.value as AlertStatus)}
            style={{
              background: '#0f172a', color: '#e2e8f0', border: '1px solid #334155',
              padding: '6px 10px', borderRadius: 4, marginTop: 4, fontSize: 13,
            }}
          >
            {STATUSES.map(s => (
              <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
            ))}
          </select>
        </div>
        <div style={{ marginBottom: 14 }}>
          <label style={{ fontSize: 12, color: '#64748b' }}>Analyst Notes</label><br />
          <textarea
            value={notes}
            onChange={e => { setNotes(e.target.value); setSaved(false) }}
            rows={4}
            style={{
              width: '100%', background: '#0f172a', color: '#e2e8f0',
              border: '1px solid #334155', borderRadius: 4, padding: 8, marginTop: 4,
              fontFamily: 'monospace', fontSize: 13, boxSizing: 'border-box', resize: 'vertical',
            }}
          />
          <button
            onClick={() => notesMutation.mutate(notes)}
            style={{ ...btn, background: '#3b82f6', color: '#fff', border: 'none', marginTop: 6 }}
          >
            {saved ? '✓ Saved' : 'Save Notes'}
          </button>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={() => handleExport('md')} style={{ ...btn, background: '#0f172a', color: '#60a5fa' }}>
            Export Markdown
          </button>
          <button onClick={() => handleExport('json')} style={{ ...btn, background: '#0f172a', color: '#60a5fa' }}>
            Export JSON
          </button>
        </div>
        <p style={{ fontSize: 11, color: '#475569', marginTop: 12 }}>
          Note: export requires status ≠ "new" (analyst review gate — NFR7)
        </p>
      </section>

      <button
        onClick={() => navigate('/')}
        style={{ ...btn, background: '#0f172a', color: '#64748b', marginTop: 8 }}
      >
        ← Back to alerts
      </button>
    </div>
  )
}
