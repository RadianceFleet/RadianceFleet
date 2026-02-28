import { useRef, useState } from 'react'
import { useWatchlist, useImportWatchlist, useRemoveWatchlistEntry } from '../hooks/useWatchlist'
import { Spinner } from '../components/ui/Spinner'
import { Card } from '../components/ui/Card'
import { EmptyState } from '../components/ui/EmptyState'
import { Pagination } from '../components/ui/Pagination'

const PAGE_SIZE = 20
const SOURCES = ['OFAC', 'KSE', 'OpenSanctions'] as const

const cellStyle: React.CSSProperties = { padding: '0.5rem 0.75rem', fontSize: '0.8125rem' }
const headStyle: React.CSSProperties = {
  ...cellStyle,
  fontWeight: 600,
  color: 'var(--text-muted)',
  textAlign: 'left',
  borderBottom: '1px solid var(--border)',
}

export function WatchlistPage() {
  const [page, setPage] = useState(0)
  const { data, isLoading, error } = useWatchlist({ skip: page * PAGE_SIZE, limit: PAGE_SIZE })
  const entries = data?.items
  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const importMutation = useImportWatchlist()
  const removeMutation = useRemoveWatchlistEntry()

  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [source, setSource] = useState<string>(SOURCES[0])

  const handleImport = () => {
    if (!file) return
    importMutation.mutate({ file, source })
  }

  return (
    <div style={{ maxWidth: 900 }}>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>
        Watchlist Management
      </h2>

      <Card style={{ marginBottom: '1rem' }}>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Import Watchlist</h3>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.json"
            style={{ display: 'none' }}
            onChange={e => {
              const f = e.target.files?.[0]
              if (f) setFile(f)
            }}
          />
          <button
            onClick={() => fileRef.current?.click()}
            style={{
              padding: '0.5rem 1rem',
              background: 'var(--bg-base)',
              color: 'var(--text-body)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              cursor: 'pointer',
              fontSize: '0.8125rem',
            }}
          >
            {file ? file.name : 'Choose file...'}
          </button>

          <select
            value={source}
            onChange={e => setSource(e.target.value)}
            style={{
              padding: '0.5rem',
              background: 'var(--bg-base)',
              color: 'var(--text-body)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius)',
              fontSize: '0.8125rem',
            }}
          >
            {SOURCES.map(s => <option key={s} value={s}>{s}</option>)}
          </select>

          <button
            onClick={handleImport}
            disabled={!file || importMutation.isPending}
            style={{
              padding: '0.5rem 1rem',
              background: file ? 'var(--accent-primary)' : 'var(--border)',
              color: 'white',
              border: 'none',
              borderRadius: 'var(--radius)',
              cursor: file ? 'pointer' : 'not-allowed',
              fontSize: '0.8125rem',
            }}
          >
            Import
          </button>
          {importMutation.isPending && <Spinner text="Importing..." />}
        </div>

        {importMutation.isError && (
          <p style={{ color: 'var(--score-critical)', fontSize: '0.8125rem', marginTop: '0.5rem' }}>
            {importMutation.error.message}
          </p>
        )}
        {importMutation.isSuccess && (
          <p style={{ color: 'var(--score-low)', fontSize: '0.8125rem', marginTop: '0.5rem' }}>
            Import complete
          </p>
        )}
      </Card>

      <Card>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Watchlist Entries</h3>
        {isLoading && <Spinner text="Loading watchlist..." />}
        {error && <p style={{ color: 'var(--score-critical)', fontSize: '0.875rem' }}>Failed to load watchlist</p>}

        {entries && entries.length === 0 && (
          <EmptyState title="No watchlist entries" description="Import a watchlist file to get started" />
        )}

        {entries && entries.length > 0 && (
          <>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ background: 'var(--bg-base)' }}>
                    <th style={headStyle}>Vessel</th>
                    <th style={headStyle}>MMSI</th>
                    <th style={headStyle}>Source</th>
                    <th style={headStyle}>Reason</th>
                    <th style={headStyle}>Listed</th>
                    <th style={headStyle}>Active</th>
                    <th style={headStyle}></th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map(entry => (
                    <tr key={entry.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={cellStyle}>{entry.vessel_name ?? '-'}</td>
                      <td style={{ ...cellStyle, fontFamily: 'monospace' }}>{entry.mmsi ?? '-'}</td>
                      <td style={cellStyle}>{entry.source}</td>
                      <td style={{ ...cellStyle, color: 'var(--text-dim)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {entry.reason ?? '-'}
                      </td>
                      <td style={cellStyle}>{entry.listed_date?.slice(0, 10) ?? '-'}</td>
                      <td style={cellStyle}>
                        <span style={{ color: entry.is_active ? 'var(--score-low)' : 'var(--text-dim)' }}>
                          {entry.is_active ? 'Yes' : 'No'}
                        </span>
                      </td>
                      <td style={cellStyle}>
                        <button
                          onClick={() => removeMutation.mutate(entry.id)}
                          disabled={removeMutation.isPending}
                          style={{
                            padding: '0.25rem 0.5rem',
                            background: 'transparent',
                            color: 'var(--score-critical)',
                            border: '1px solid var(--score-critical)',
                            borderRadius: 'var(--radius)',
                            cursor: 'pointer',
                            fontSize: '0.75rem',
                          }}
                        >
                          Remove
                        </button>
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
              label="entries"
            />
          </>
        )}
      </Card>
    </div>
  )
}
