import { useParams, Link, useNavigate } from 'react-router-dom'
import { useState, useEffect } from 'react'
import type { AlertStatus, ExportResponse } from '../types/api'
import { useAlert, useUpdateAlertStatus, useUpdateAlertNotes } from '../hooks/useAlerts'
import { apiFetch } from '../lib/api'
import { AlertMap } from './AlertMap'
import { Spinner } from './ui/Spinner'
import { ScoreBadge } from './ui/ScoreBadge'

const STATUSES: AlertStatus[] = ['new', 'under_review', 'needs_satellite_check', 'documented', 'dismissed']

const card: React.CSSProperties = { background: 'var(--bg-card)', borderRadius: 'var(--radius-md)', padding: 16, marginBottom: 16 }
const sectionHead: React.CSSProperties = { margin: '0 0 12px', fontSize: 14, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1 }
const labelCell: React.CSSProperties = { color: 'var(--text-dim)', width: 180, fontSize: 13, paddingRight: 12, paddingBottom: 8, verticalAlign: 'top' }
const valueCell: React.CSSProperties = { fontSize: 13, paddingBottom: 8 }
const btn: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: '6px 14px', cursor: 'pointer', fontSize: 13 }

export function AlertDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [notes, setNotes] = useState('')
  const [saved, setSaved] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)
  const [satLoading, setSatLoading] = useState(false)
  const [satResult, setSatResult] = useState<string | null>(null)

  const { data: alert, isLoading, error } = useAlert(id)
  const statusMutation = useUpdateAlertStatus(id ?? '')
  const notesMutation = useUpdateAlertNotes(id ?? '')

  useEffect(() => {
    if (alert?.analyst_notes) setNotes(alert.analyst_notes)
  }, [alert?.analyst_notes])

  // Auto-dismiss "Saved" after 3 seconds
  useEffect(() => {
    if (!saved) return
    const timer = setTimeout(() => setSaved(false), 3000)
    return () => clearTimeout(timer)
  }, [saved])

  const handleExport = async (fmt: 'md' | 'json') => {
    setExportError(null)
    try {
      const data = await apiFetch<ExportResponse>(`/alerts/${id}/export?format=${fmt}`, { method: 'POST' })
      const content = data.content ?? JSON.stringify(data, null, 2)
      const blob = new Blob([content], { type: fmt === 'json' ? 'application/json' : 'text/markdown' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `alert_${id}.${fmt}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setExportError(err instanceof Error ? err.message : 'Export failed')
    }
  }

  const handleSatelliteCheck = async () => {
    setSatLoading(true)
    setSatResult(null)
    try {
      await apiFetch(`/alerts/${id}/satellite-check`, { method: 'POST' })
      setSatResult('Satellite check prepared')
    } catch (err) {
      setSatResult(err instanceof Error ? err.message : 'Failed to prepare satellite check')
    } finally {
      setSatLoading(false)
    }
  }

  if (isLoading) return <Spinner text="Loading alert…" />
  if (error || !alert) {
    return (
      <p style={{ color: 'var(--score-critical)' }}>
        Alert not found.{' '}
        <Link to="/alerts">← Back</Link>
      </p>
    )
  }

  return (
    <div style={{ maxWidth: 860 }}>
      <Link to="/alerts" style={{ fontSize: 13 }}>← All alerts</Link>
      <h2 style={{ margin: '12px 0 4px', fontSize: 18 }}>Alert #{alert.gap_event_id}</h2>
      <p style={{ color: 'var(--text-dim)', margin: '0 0 20px', fontSize: 13 }}>
        {alert.vessel_id ? (
          <Link to={`/vessels/${alert.vessel_id}`}>{alert.vessel_name ?? 'Unknown vessel'}</Link>
        ) : (
          alert.vessel_name ?? 'Unknown vessel'
        )}
        {' · MMSI '}{alert.vessel_mmsi ?? '?'}
        {' · '}{alert.vessel_flag ?? '?'}
        {alert.vessel_deadweight != null && ` · ${alert.vessel_deadweight.toLocaleString()} DWT`}
        {alert.corridor_name && ` · ${alert.corridor_name}`}
      </p>

      <AlertMap
        lastPoint={alert.last_point}
        firstPointAfter={alert.first_point_after}
        envelope={alert.movement_envelope}
        corridorId={alert.corridor_id ?? undefined}
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
          <ScoreBadge score={alert.risk_score} size="md" />
        </h3>
        {alert.risk_breakdown_json && Object.keys(alert.risk_breakdown_json).length > 0 && (
          <details>
            <summary style={{ cursor: 'pointer', color: 'var(--text-muted)', fontSize: 12 }}>Score breakdown ▾</summary>
            <pre style={{ fontSize: 11, color: 'var(--text-body)', overflowX: 'auto', maxHeight: 260, marginTop: 8 }}>
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
              style={{ fontSize: 13 }}
            >
              Open Copernicus Browser ↗
            </a>
          )}
        </section>
      )}

      {/* Error banners */}
      {statusMutation.error && (
        <div style={{ background: 'var(--score-critical)', color: 'white', padding: '8px 12px', borderRadius: 'var(--radius)', marginBottom: 12, fontSize: 13 }}>
          Status update failed: {statusMutation.error instanceof Error ? statusMutation.error.message : 'Unknown error'}
        </div>
      )}
      {notesMutation.error && (
        <div style={{ background: 'var(--score-critical)', color: 'white', padding: '8px 12px', borderRadius: 'var(--radius)', marginBottom: 12, fontSize: 13 }}>
          Notes save failed: {notesMutation.error instanceof Error ? notesMutation.error.message : 'Unknown error'}
        </div>
      )}

      <section style={card}>
        <h3 style={sectionHead}>Analyst Workflow</h3>
        <div style={{ marginBottom: 14 }}>
          <label style={{ fontSize: 12, color: 'var(--text-dim)' }}>Status</label><br />
          <select
            value={alert.status}
            onChange={e => statusMutation.mutate({ status: e.target.value })}
            disabled={statusMutation.isPending}
            style={{
              background: 'var(--bg-base)', color: 'var(--text-bright)', border: '1px solid var(--border)',
              padding: '6px 10px', borderRadius: 'var(--radius)', marginTop: 4, fontSize: 13,
              opacity: statusMutation.isPending ? 0.6 : 1,
            }}
          >
            {STATUSES.map(s => (
              <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>
            ))}
          </select>
          {statusMutation.isPending && <span style={{ fontSize: 12, color: 'var(--text-muted)', marginLeft: 8 }}>Saving…</span>}
        </div>
        <div style={{ marginBottom: 14 }}>
          <label style={{ fontSize: 12, color: 'var(--text-dim)' }}>Analyst Notes</label><br />
          <textarea
            value={notes}
            onChange={e => { setNotes(e.target.value); setSaved(false) }}
            rows={4}
            style={{
              width: '100%', background: 'var(--bg-base)', color: 'var(--text-bright)',
              border: '1px solid var(--border)', borderRadius: 'var(--radius)', padding: 8, marginTop: 4,
              fontFamily: 'monospace', fontSize: 13, boxSizing: 'border-box', resize: 'vertical',
            }}
          />
          <button
            onClick={() => notesMutation.mutate(notes)}
            disabled={notesMutation.isPending}
            style={{
              ...btn,
              background: 'var(--accent-primary)',
              color: '#fff',
              border: 'none',
              marginTop: 6,
              opacity: notesMutation.isPending ? 0.6 : 1,
            }}
          >
            {notesMutation.isPending ? 'Saving…' : saved ? '✓ Saved' : 'Save Notes'}
          </button>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button onClick={() => handleExport('md')} style={{ ...btn, background: 'var(--bg-base)', color: 'var(--accent)' }}>
            Export Markdown
          </button>
          <button onClick={() => handleExport('json')} style={{ ...btn, background: 'var(--bg-base)', color: 'var(--accent)' }}>
            Export JSON
          </button>
          <button
            onClick={handleSatelliteCheck}
            disabled={satLoading}
            style={{ ...btn, background: 'var(--bg-base)', color: 'var(--warning)', opacity: satLoading ? 0.6 : 1 }}
          >
            {satLoading ? 'Preparing…' : 'Prepare satellite check'}
          </button>
        </div>
        {exportError && (
          <p style={{ fontSize: 12, color: 'var(--score-critical)', marginTop: 8 }}>{exportError}</p>
        )}
        {satResult && (
          <p style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 8 }}>{satResult}</p>
        )}
        <p style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 12 }}>
          Note: export requires status ≠ "new" (analyst review gate — NFR7)
        </p>
      </section>

      <button
        onClick={() => navigate('/alerts')}
        style={{ ...btn, background: 'var(--bg-base)', color: 'var(--text-dim)', marginTop: 8 }}
      >
        ← Back to alerts
      </button>
    </div>
  )
}
