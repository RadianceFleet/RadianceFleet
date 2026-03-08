import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'

interface SourceStatus {
  last_seen: string | null
  age_minutes: number | null
  status: string
  record_count?: number
}

interface FreshnessData {
  status: string
  sources: Record<string, SourceStatus>
}

interface CollectionStatus {
  sources: Record<string, {
    last_run: string | null
    total_runs: number
    last_success: boolean
  }>
}

function statusColor(status: string): string {
  if (status === 'ok' || status === 'fresh') return '#16a34a'
  if (status === 'stale') return '#ea580c'
  if (status === 'offline') return '#dc2626'
  return '#6b7280'
}

export function DataHealthPage() {
  const freshness = useQuery({
    queryKey: ['data-freshness'],
    queryFn: () => apiFetch<FreshnessData>('/health/data-freshness'),
  })
  const collection = useQuery({
    queryKey: ['collection-status'],
    queryFn: () => apiFetch<CollectionStatus>('/health/collection-status'),
  })

  return (
    <div style={{ maxWidth: 900 }}>
      <h2 style={{ margin: '0 0 4px', fontSize: 18 }}>Data Health</h2>
      <p style={{ color: 'var(--text-dim)', margin: '0 0 20px', fontSize: 13 }}>
        AIS feed status and data freshness across all sources.
      </p>

      <Card style={{ marginBottom: 16 }}>
        <h3 style={{ margin: '0 0 12px', fontSize: 14, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1 }}>
          Source Freshness
        </h3>
        {freshness.isLoading && <Spinner text="Loading..." />}
        {freshness.data?.sources && (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                <th style={{ textAlign: 'left', padding: '6px 8px', fontSize: 12, color: 'var(--text-muted)' }}>Source</th>
                <th style={{ textAlign: 'left', padding: '6px 8px', fontSize: 12, color: 'var(--text-muted)' }}>Status</th>
                <th style={{ textAlign: 'left', padding: '6px 8px', fontSize: 12, color: 'var(--text-muted)' }}>Last Seen</th>
                <th style={{ textAlign: 'right', padding: '6px 8px', fontSize: 12, color: 'var(--text-muted)' }}>Age</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(freshness.data.sources).map(([name, s]) => (
                <tr key={name} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '6px 8px', fontSize: 13 }}>{name}</td>
                  <td style={{ padding: '6px 8px' }}>
                    <span style={{ color: statusColor(s.status), fontWeight: 600, fontSize: 12, textTransform: 'uppercase' }}>
                      {s.status}
                    </span>
                  </td>
                  <td style={{ padding: '6px 8px', fontSize: 12, color: 'var(--text-muted)' }}>
                    {s.last_seen ? s.last_seen.slice(0, 19).replace('T', ' ') : 'Never'}
                  </td>
                  <td style={{ padding: '6px 8px', fontSize: 12, color: 'var(--text-muted)', textAlign: 'right' }}>
                    {s.age_minutes != null ? `${Math.round(s.age_minutes)}m` : '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card>
        <h3 style={{ margin: '0 0 12px', fontSize: 14, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: 1 }}>
          Collection Runs
        </h3>
        {collection.isLoading && <Spinner text="Loading..." />}
        {collection.data?.sources && (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                <th style={{ textAlign: 'left', padding: '6px 8px', fontSize: 12, color: 'var(--text-muted)' }}>Source</th>
                <th style={{ textAlign: 'left', padding: '6px 8px', fontSize: 12, color: 'var(--text-muted)' }}>Last Run</th>
                <th style={{ textAlign: 'right', padding: '6px 8px', fontSize: 12, color: 'var(--text-muted)' }}>Total Runs</th>
                <th style={{ textAlign: 'center', padding: '6px 8px', fontSize: 12, color: 'var(--text-muted)' }}>Last Status</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(collection.data.sources).map(([name, s]) => (
                <tr key={name} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '6px 8px', fontSize: 13 }}>{name}</td>
                  <td style={{ padding: '6px 8px', fontSize: 12, color: 'var(--text-muted)' }}>
                    {s.last_run ? s.last_run.slice(0, 19).replace('T', ' ') : 'Never'}
                  </td>
                  <td style={{ padding: '6px 8px', fontSize: 12, textAlign: 'right' }}>{s.total_runs}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'center' }}>
                    <span style={{ color: s.last_success ? '#16a34a' : '#dc2626', fontSize: 12 }}>
                      {s.last_success ? 'OK' : 'FAIL'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  )
}
