import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useStsEvents } from '../hooks/useStsEvents'
import { useStsValidation } from '../hooks/useStsValidation'
import { useStsChains } from '../hooks/useStsChains'
import { StsChainDiagram } from '../components/charts/StsChainDiagram'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import { EmptyState } from '../components/ui/EmptyState'
import { Pagination } from '../components/ui/Pagination'

const PAGE_SIZE = 20

const cellStyle: React.CSSProperties = { padding: '0.5rem 0.75rem', fontSize: '0.8125rem' }
const headStyle: React.CSSProperties = {
  ...cellStyle,
  fontWeight: 600,
  color: 'var(--text-muted)',
  textAlign: 'left' as const,
  borderBottom: '1px solid var(--border)',
}
const tabStyle = (active: boolean): React.CSSProperties => ({
  padding: '0.5rem 1rem',
  cursor: 'pointer',
  borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
  color: active ? 'var(--accent)' : 'var(--text-muted)',
  background: 'none',
  border: 'none',
  fontSize: '0.875rem',
  fontWeight: active ? 600 : 400,
})

function formatTimestamp(ts: string | null | undefined): string {
  if (!ts) return '-'
  return ts.slice(0, 16).replace('T', ' ')
}

type Tab = 'events' | 'chains'

export function StsEventsPage() {
  const [tab, setTab] = useState<Tab>('events')

  return (
    <div style={{ maxWidth: 1100 }}>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        STS Transfer Events
      </h2>

      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--border)', marginBottom: '1rem' }}>
        <button style={tabStyle(tab === 'events')} onClick={() => setTab('events')}>Events</button>
        <button style={tabStyle(tab === 'chains')} onClick={() => setTab('chains')}>Chains</button>
      </div>

      {tab === 'events' && <EventsTab />}
      {tab === 'chains' && <ChainsTab />}
    </div>
  )
}

function ChainsTab() {
  const [page, setPage] = useState(0)
  const { data, isLoading, error } = useStsChains({ skip: page * PAGE_SIZE, limit: PAGE_SIZE })
  const chains = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <Card>
      {isLoading && <Spinner text="Loading STS chains..." />}
      {error && <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem' }}>Failed to load STS chains</p>}
      {chains.length === 0 && !isLoading && !error && (
        <EmptyState title="No STS relay chains" description="Relay chain alerts will appear here once detected" />
      )}
      {chains.length > 0 && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <StsChainDiagram chains={chains} />
          </div>
          <Pagination page={page} totalPages={totalPages} total={total} onPageChange={setPage} label="chains" />
        </>
      )}
    </Card>
  )
}

function EventsTab() {
  const [page, setPage] = useState(0)
  const { data, isLoading, error } = useStsEvents({ skip: page * PAGE_SIZE, limit: PAGE_SIZE })
  const validation = useStsValidation()
  const events = data?.items
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
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
          <>
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
                    <th style={headStyle}>Validated</th>
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
                      <td style={cellStyle}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <span style={{ fontSize: '0.875rem' }}>
                            {evt.user_validated === true
                              ? '\u2705'
                              : evt.user_validated === false
                                ? '\u274C'
                                : '\u2014'}
                          </span>
                          <button
                            title="Confirm"
                            onClick={() => validation.mutate({ stsId: evt.sts_id, user_validated: true })}
                            style={{
                              border: '1px solid var(--border)',
                              borderRadius: 'var(--radius)',
                              padding: '1px 6px',
                              cursor: 'pointer',
                              fontSize: '0.75rem',
                              background: 'var(--bg-base)',
                              color: 'var(--score-low, #22c55e)',
                            }}
                          >
                            Confirm
                          </button>
                          <button
                            title="Reject"
                            onClick={() => validation.mutate({ stsId: evt.sts_id, user_validated: false })}
                            style={{
                              border: '1px solid var(--border)',
                              borderRadius: 'var(--radius)',
                              padding: '1px 6px',
                              cursor: 'pointer',
                              fontSize: '0.75rem',
                              background: 'var(--bg-base)',
                              color: 'var(--score-critical, #ef4444)',
                            }}
                          >
                            Reject
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <Pagination
              page={page}
              totalPages={totalPages}
              total={total}
              onPageChange={setPage}
              label="events"
            />
          </>
        )}
      </Card>
  )
}
