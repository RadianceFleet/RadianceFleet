import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import { EmptyState } from '../components/ui/EmptyState'
import { Pagination } from '../components/ui/Pagination'
import { useGlobalSpoofing } from '../hooks/useGlobalDetections'
import { useGlobalLoitering } from '../hooks/useLoitering'
import { useStsChains } from '../hooks/useStsChains'

const PAGE_SIZE = 50

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

type Tab = 'spoofing' | 'loitering' | 'sts'

export function GlobalDetectionsPage() {
  const [tab, setTab] = useState<Tab>('spoofing')

  return (
    <div style={{ maxWidth: 1100 }}>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        Global Detections
      </h2>

      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', marginBottom: '1rem' }}>
        <button style={tabStyle(tab === 'spoofing')} onClick={() => setTab('spoofing')}>Spoofing</button>
        <button style={tabStyle(tab === 'loitering')} onClick={() => setTab('loitering')}>Loitering</button>
        <button style={tabStyle(tab === 'sts')} onClick={() => setTab('sts')}>STS Chains</button>
      </div>

      {tab === 'spoofing' && <SpoofingTab />}
      {tab === 'loitering' && <LoiteringTab />}
      {tab === 'sts' && <StsTab />}
    </div>
  )
}

function SpoofingTab() {
  const [page, setPage] = useState(0)
  const { data, isLoading, error } = useGlobalSpoofing({ skip: page * PAGE_SIZE, limit: PAGE_SIZE })
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <Card>
      {isLoading && <Spinner text="Loading spoofing events..." />}
      {error && <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem' }}>Failed to load spoofing events</p>}

      {items.length === 0 && !isLoading && !error && (
        <EmptyState title="No spoofing events" description="No spoofing anomalies detected yet" />
      )}

      {items.length > 0 && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={headStyle}>Anomaly ID</th>
                  <th style={headStyle}>Vessel ID</th>
                  <th style={headStyle}>Type</th>
                  <th style={headStyle}>Start Time</th>
                  <th style={headStyle}>Risk Score</th>
                </tr>
              </thead>
              <tbody>
                {items.map((e) => (
                  <tr key={e.anomaly_id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ ...cellStyle, fontFamily: 'monospace' }}>{e.anomaly_id}</td>
                    <td style={cellStyle}>
                      <Link to={`/vessels/${e.vessel_id}`} style={{ color: 'var(--accent)', textDecoration: 'none' }}>
                        {e.vessel_id}
                      </Link>
                    </td>
                    <td style={cellStyle}>{e.anomaly_type}</td>
                    <td style={cellStyle}>{formatTimestamp(e.start_time_utc)}</td>
                    <td style={cellStyle}>{e.risk_score_component}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <Pagination page={page} totalPages={totalPages} total={total} onPageChange={setPage} label="spoofing events" />
          )}
        </>
      )}
    </Card>
  )
}

function LoiteringTab() {
  const [page, setPage] = useState(0)
  const { data, isLoading, error } = useGlobalLoitering({ skip: page * PAGE_SIZE, limit: PAGE_SIZE })
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <Card>
      {isLoading && <Spinner text="Loading loitering events..." />}
      {error && <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem' }}>Failed to load loitering events</p>}

      {items.length === 0 && !isLoading && !error && (
        <EmptyState title="No loitering events" description="No loitering events detected yet" />
      )}

      {items.length > 0 && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={headStyle}>Loiter ID</th>
                  <th style={headStyle}>Vessel ID</th>
                  <th style={headStyle}>Duration (hours)</th>
                  <th style={headStyle}>Location</th>
                  <th style={headStyle}>Corridor ID</th>
                  <th style={headStyle}>Start Time</th>
                </tr>
              </thead>
              <tbody>
                {items.map((e) => (
                  <tr key={e.loiter_id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ ...cellStyle, fontFamily: 'monospace' }}>{e.loiter_id}</td>
                    <td style={cellStyle}>
                      <Link to={`/vessels/${e.vessel_id}`} style={{ color: 'var(--accent)', textDecoration: 'none' }}>
                        {e.vessel_id}
                      </Link>
                    </td>
                    <td style={cellStyle}>{e.duration_hours?.toFixed(1) ?? '-'}</td>
                    <td style={cellStyle}>
                      {e.mean_lat != null && e.mean_lon != null
                        ? `${e.mean_lat.toFixed(4)}, ${e.mean_lon.toFixed(4)}`
                        : '-'}
                    </td>
                    <td style={cellStyle}>{e.corridor_id ?? '-'}</td>
                    <td style={cellStyle}>{formatTimestamp(e.start_time_utc)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <Pagination page={page} totalPages={totalPages} total={total} onPageChange={setPage} label="loitering events" />
          )}
        </>
      )}
    </Card>
  )
}

function StsTab() {
  const [page, setPage] = useState(0)
  const { data, isLoading, error } = useStsChains({ skip: page * PAGE_SIZE, limit: PAGE_SIZE })
  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <Card>
      {isLoading && <Spinner text="Loading STS chains..." />}
      {error && <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem' }}>Failed to load STS chains</p>}

      {items.length === 0 && !isLoading && !error && (
        <EmptyState title="No STS chains" description="No STS relay chains detected yet" />
      )}

      {items.length > 0 && (
        <>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={headStyle}>Alert ID</th>
                  <th style={headStyle}>Chain Length</th>
                  <th style={headStyle}>Hops</th>
                  <th style={headStyle}>Risk Score</th>
                  <th style={headStyle}>Created</th>
                </tr>
              </thead>
              <tbody>
                {items.map((e) => (
                  <tr key={e.alert_id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ ...cellStyle, fontFamily: 'monospace' }}>{e.alert_id}</td>
                    <td style={cellStyle}>{e.chain_length}</td>
                    <td style={cellStyle}>{e.hops?.length ?? 0}</td>
                    <td style={cellStyle}>{e.risk_score_component}</td>
                    <td style={cellStyle}>{formatTimestamp(e.created_utc)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <Pagination page={page} totalPages={totalPages} total={total} onPageChange={setPage} label="STS chains" />
          )}
        </>
      )}
    </Card>
  )
}

export default GlobalDetectionsPage
