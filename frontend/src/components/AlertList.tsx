import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useAlerts, useBulkUpdateAlertStatus, type AlertFilters } from '../hooks/useAlerts'
import { useToast } from './ui/Toast'
import { ScoreBadge } from './ui/ScoreBadge'
import { StatusBadge } from './ui/StatusBadge'
import { Spinner } from './ui/Spinner'
import { EmptyState } from './ui/EmptyState'
import { AlertFilterBar } from './AlertFilterBar'
import { AlertBulkActions } from './AlertBulkActions'
import { useAuth } from '../hooks/useAuth'
import { thSortable as thStyle, tdStyle, btnStyle, tableStyle, theadRow, tbodyRow } from '../styles/tables'

const PAGE_SIZE = 50

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
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [patternsOnly, setPatternsOnly] = useState(false)
  const [bulkStatus, setBulkStatus] = useState('under_review')
  const bulkUpdate = useBulkUpdateAlertStatus()
  const { addToast } = useToast()
  const { isAuthenticated } = useAuth()

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
  const displayAlerts = patternsOnly ? alerts.filter(a => a.is_recurring_pattern) : alerts
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

      {/* Extracted: filter bar */}
      <AlertFilterBar
        minScore={minScore}
        onMinScoreChange={v => { setMinScore(v); setPage(0) }}
        status={status}
        onStatusChange={v => { setStatus(v); setPage(0) }}
        vesselName={vesselName}
        onVesselNameChange={v => { setVesselName(v); setPage(0) }}
        dateFrom={dateFrom}
        onDateFromChange={v => { setDateFrom(v); setPage(0) }}
        dateTo={dateTo}
        onDateToChange={v => { setDateTo(v); setPage(0) }}
        patternsOnly={patternsOnly}
        onPatternsOnlyToggle={() => setPatternsOnly(p => !p)}
        exportFilters={exportFilters}
      />

      {isLoading && <Spinner text="Loading alerts…" />}
      {error && <p style={{ color: 'var(--score-critical)' }}>Error loading alerts.</p>}
      {!isLoading && !error && displayAlerts.length === 0 && (
        <EmptyState title="No alerts found" description={patternsOnly ? 'No recurring patterns found. Try disabling the patterns filter.' : 'Adjust filters or run: make detect'} />
      )}

      {displayAlerts.length > 0 && (
        <>
          {/* Extracted: bulk action bar */}
          {isAuthenticated && (
            <AlertBulkActions
              selected={selected}
              onClearSelection={() => setSelected(new Set())}
              bulkStatus={bulkStatus}
              onBulkStatusChange={setBulkStatus}
              bulkUpdate={bulkUpdate}
              addToast={addToast}
            />
          )}

          <table style={{ ...tableStyle, fontSize: '0.875rem' }}>
            <thead>
              <tr style={theadRow}>
                {isAuthenticated && (
                  <th style={{ ...thStyle, width: 36 }}>
                    <input
                      type="checkbox"
                      checked={displayAlerts.length > 0 && selected.size === displayAlerts.length}
                      onChange={e => {
                        if (e.target.checked) {
                          setSelected(new Set(displayAlerts.map(a => a.gap_event_id)))
                        } else {
                          setSelected(new Set())
                        }
                      }}
                    />
                  </th>
                )}
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
              {displayAlerts.map(a => (
                <tr key={a.gap_event_id} style={tbodyRow}>
                  {isAuthenticated && (
                    <td style={tdStyle}>
                      <input
                        type="checkbox"
                        checked={selected.has(a.gap_event_id)}
                        onChange={e => {
                          const next = new Set(selected)
                          if (e.target.checked) next.add(a.gap_event_id)
                          else next.delete(a.gap_event_id)
                          setSelected(next)
                        }}
                      />
                    </td>
                  )}
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
                    {a.impossible_speed_flag && <span title="Impossible speed">!!</span>}
                    {a.in_dark_zone && <span title="Dark zone">DZ</span>}
                    {a.is_recurring_pattern && (
                      <span style={{
                        display: 'inline-block',
                        padding: '2px 6px',
                        borderRadius: 'var(--radius)',
                        fontSize: 10,
                        fontWeight: 700,
                        background: '#9b59b6',
                        color: 'white',
                        marginLeft: 4,
                      }}>
                        Recurring{a.prior_similar_count != null ? ` (${a.prior_similar_count} in 90d)` : ''}
                      </span>
                    )}
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
