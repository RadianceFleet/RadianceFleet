import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useVesselSearch } from '../hooks/useVessels'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'
import { ScoreBadge } from '../components/ui/ScoreBadge'
import { EmptyState } from '../components/ui/EmptyState'

const cellStyle: React.CSSProperties = { padding: '0.5rem 0.75rem', fontSize: '0.8125rem' }
const headStyle: React.CSSProperties = {
  ...cellStyle,
  fontWeight: 600,
  color: 'var(--text-muted)',
  textAlign: 'left' as const,
  borderBottom: '1px solid var(--border)',
}

function useDebouncedValue(value: string, delayMs: number) {
  const [debounced, setDebounced] = useState(value)
  const timerRef = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => {
    timerRef.current = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timerRef.current)
  }, [value, delayMs])

  return debounced
}

export function VesselSearchPage() {
  const [search, setSearch] = useState('')
  const [flag, setFlag] = useState('')
  const [vesselType, setVesselType] = useState('')

  const debouncedSearch = useDebouncedValue(search, 300)

  const filters = {
    search: debouncedSearch || undefined,
    flag: flag || undefined,
    vessel_type: vesselType || undefined,
    limit: 20,
  }

  const { data: vessels, isLoading, error } = useVesselSearch(filters)

  const hasFilters = !!(debouncedSearch || flag || vesselType)

  return (
    <div style={{ maxWidth: 1000 }}>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        Vessel Search
      </h2>

      <Card style={{ marginBottom: '1rem' }}>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            type="text"
            placeholder="Search MMSI, IMO, or name..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              flex: 1,
              minWidth: 200,
              padding: '0.5rem 0.75rem',
              background: 'var(--bg-base)',
              color: 'var(--text-body)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              fontSize: '0.8125rem',
              outline: 'none',
            }}
          />
          <input
            type="text"
            placeholder="Flag (e.g. PA)"
            value={flag}
            onChange={e => setFlag(e.target.value)}
            style={{
              width: 120,
              padding: '0.5rem 0.75rem',
              background: 'var(--bg-base)',
              color: 'var(--text-body)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              fontSize: '0.8125rem',
              outline: 'none',
            }}
          />
          <input
            type="text"
            placeholder="Vessel type"
            value={vesselType}
            onChange={e => setVesselType(e.target.value)}
            style={{
              width: 160,
              padding: '0.5rem 0.75rem',
              background: 'var(--bg-base)',
              color: 'var(--text-body)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              fontSize: '0.8125rem',
              outline: 'none',
            }}
          />
        </div>
      </Card>

      <Card>
        {!hasFilters && (
          <EmptyState
            title="Search for vessels"
            description="Enter a MMSI, IMO, name, flag, or vessel type to begin"
          />
        )}

        {hasFilters && isLoading && <Spinner text="Searching vessels..." />}

        {hasFilters && error && (
          <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem', padding: '1rem' }}>
            Failed to search vessels
          </p>
        )}

        {hasFilters && vessels && vessels.length === 0 && (
          <EmptyState title="No vessels found" description="Try adjusting your search filters" />
        )}

        {vessels && vessels.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--bg-base)' }}>
                  <th style={headStyle}>MMSI</th>
                  <th style={headStyle}>Name</th>
                  <th style={headStyle}>Flag</th>
                  <th style={headStyle}>Type</th>
                  <th style={headStyle}>DWT</th>
                  <th style={headStyle}>Last Score</th>
                  <th style={headStyle}>Watchlist</th>
                </tr>
              </thead>
              <tbody>
                {vessels.map(v => (
                  <tr key={String(v.vessel_id)} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ ...cellStyle, fontFamily: 'monospace' }}>
                      <Link
                        to={`/vessels/${v.vessel_id}`}
                        style={{ color: 'var(--accent)', textDecoration: 'none' }}
                      >
                        {String(v.mmsi ?? '-')}
                      </Link>
                    </td>
                    <td style={cellStyle}>
                      <Link
                        to={`/vessels/${v.vessel_id}`}
                        style={{ color: 'var(--text-bright)', textDecoration: 'none' }}
                      >
                        {String(v.name ?? '-')}
                      </Link>
                    </td>
                    <td style={cellStyle}>{String(v.flag ?? '-')}</td>
                    <td style={{ ...cellStyle, color: 'var(--text-dim)' }}>
                      {String(v.vessel_type ?? '-')}
                    </td>
                    <td style={{ ...cellStyle, fontFamily: 'monospace' }}>
                      {v.deadweight != null ? Number(v.deadweight).toLocaleString() : '-'}
                    </td>
                    <td style={cellStyle}>
                      {v.last_risk_score != null ? (
                        <ScoreBadge score={Number(v.last_risk_score)} size="sm" />
                      ) : (
                        <span style={{ color: 'var(--text-dim)' }}>-</span>
                      )}
                    </td>
                    <td style={cellStyle}>
                      {v.watchlist_status ? (
                        <span style={{
                          display: 'inline-block',
                          padding: '0.125rem 0.375rem',
                          background: 'var(--score-critical)',
                          color: 'white',
                          borderRadius: 'var(--radius)',
                          fontSize: '0.75rem',
                          fontWeight: 600,
                        }}>
                          LISTED
                        </span>
                      ) : (
                        <span style={{ color: 'var(--text-dim)' }}>-</span>
                      )}
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
