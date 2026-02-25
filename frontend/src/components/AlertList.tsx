import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAlerts, type AlertFilters } from '../hooks/useAlerts'
import { ScoreBadge } from './ui/ScoreBadge'
import { StatusBadge } from './ui/StatusBadge'
import { Spinner } from './ui/Spinner'
import { EmptyState } from './ui/EmptyState'
import { ExportButton } from './ExportButton'

const PAGE_SIZE = 50

const inputStyle: React.CSSProperties = {
  background: 'var(--bg-base)',
  color: 'var(--text-bright)',
  border: '1px solid var(--border)',
  padding: '6px 10px',
  borderRadius: 'var(--radius)',
  fontSize: '0.8125rem',
}

const thStyle: React.CSSProperties = {
  padding: '8px 12px',
  textAlign: 'left',
  fontWeight: 600,
  color: 'var(--text-muted)',
  cursor: 'pointer',
  userSelect: 'none',
  whiteSpace: 'nowrap',
}

const tdStyle: React.CSSProperties = { padding: '8px 12px' }

const btnStyle: React.CSSProperties = {
  padding: '6px 14px',
  background: 'var(--bg-card)',
  color: 'var(--text-muted)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius)',
  cursor: 'pointer',
  fontSize: '0.8125rem',
}

type SortField = 'risk_score' | 'gap_start_utc' | 'duration_minutes'

export function AlertListPage() {
  const [minScore, setMinScore] = useState('')
  const [status, setStatus] = useState('')
  const [vesselName, setVesselName] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [sortBy, setSortBy] = useState<SortField>('risk_score')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(0)

  const filters: AlertFilters = {
    min_score: minScore || undefined,
    status: status || undefined,
    vessel_name: vesselName || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    sort_by: sortBy,
    sort_order: sortOrder,
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  }

  const { data, isLoading, error } = useAlerts(filters)
  const alerts = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  function toggleSort(field: SortField) {
    if (sortBy === field) {
      setSortOrder(o => o === 'desc' ? 'asc' : 'desc')
    } else {
      setSortBy(field)
      setSortOrder('desc')
    }
    setPage(0)
  }

  function sortIndicator(field: SortField) {
    if (sortBy !== field) return ''
    return sortOrder === 'desc' ? ' ▼' : ' ▲'
  }

  const exportFilters: Record<string, string | undefined> = {
    min_score: minScore || undefined,
    status: status || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
  }

  return (
    <div>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>Alert Queue</h2>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 10, marginBottom: '1rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          placeholder="Min score"
          value={minScore}
          onChange={e => { setMinScore(e.target.value); setPage(0) }}
          style={{ ...inputStyle, width: 90 }}
        />
        <select
          value={status}
          onChange={e => { setStatus(e.target.value); setPage(0) }}
          style={inputStyle}
        >
          <option value="">All statuses</option>
          <option value="new">New</option>
          <option value="under_review">Under review</option>
          <option value="needs_satellite_check">Needs satellite check</option>
          <option value="documented">Documented</option>
          <option value="dismissed">Dismissed</option>
        </select>
        <input
          placeholder="Vessel name"
          value={vesselName}
          onChange={e => { setVesselName(e.target.value); setPage(0) }}
          style={{ ...inputStyle, width: 160 }}
        />
        <input
          type="date"
          value={dateFrom}
          onChange={e => { setDateFrom(e.target.value); setPage(0) }}
          style={inputStyle}
          title="Date from"
        />
        <input
          type="date"
          value={dateTo}
          onChange={e => { setDateTo(e.target.value); setPage(0) }}
          style={inputStyle}
          title="Date to"
        />
        <ExportButton filters={exportFilters} />
      </div>

      {isLoading && <Spinner text="Loading alerts…" />}
      {error && <p style={{ color: 'var(--score-critical)' }}>Error loading alerts.</p>}
      {!isLoading && !error && alerts.length === 0 && (
        <EmptyState title="No alerts found" description="Adjust filters or run: make detect" />
      )}

      {alerts.length > 0 && (
        <>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
            <thead>
              <tr style={{ background: 'var(--bg-card)' }}>
                <th style={thStyle}>ID</th>
                <th style={thStyle} onClick={() => toggleSort('risk_score')}>
                  Score{sortIndicator('risk_score')}
                </th>
                <th style={thStyle}>Vessel</th>
                <th style={thStyle} onClick={() => toggleSort('gap_start_utc')}>
                  Gap Start (UTC){sortIndicator('gap_start_utc')}
                </th>
                <th style={thStyle} onClick={() => toggleSort('duration_minutes')}>
                  Duration{sortIndicator('duration_minutes')}
                </th>
                <th style={thStyle}>Status</th>
                <th style={thStyle}>Flags</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map(a => (
                <tr key={a.gap_event_id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={tdStyle}>
                    <Link to={`/alerts/${a.gap_event_id}`}>#{a.gap_event_id}</Link>
                  </td>
                  <td style={tdStyle}><ScoreBadge score={a.risk_score} /></td>
                  <td style={tdStyle}>
                    <Link to={`/vessels/${a.vessel_id}`}>
                      {a.vessel_name ?? `Vessel #${a.vessel_id}`}
                    </Link>
                  </td>
                  <td style={tdStyle}>{a.gap_start_utc.slice(0, 16).replace('T', ' ')}</td>
                  <td style={tdStyle}>{(a.duration_minutes / 60).toFixed(1)}h</td>
                  <td style={tdStyle}><StatusBadge status={a.status} /></td>
                  <td style={tdStyle}>
                    {a.impossible_speed_flag && <span title="Impossible speed">⚠️</span>}
                    {a.in_dark_zone && <span title="Dark zone">🌑</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Pagination */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '0.75rem' }}>
            <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
              {total} alert{total !== 1 ? 's' : ''} total
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <button
                onClick={() => setPage(p => Math.max(0, p - 1))}
                disabled={page === 0}
                style={{ ...btnStyle, opacity: page === 0 ? 0.4 : 1 }}
              >
                Prev
              </button>
              <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
                Page {page + 1} of {totalPages}
              </span>
              <button
                onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                style={{ ...btnStyle, opacity: page >= totalPages - 1 ? 0.4 : 1 }}
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
