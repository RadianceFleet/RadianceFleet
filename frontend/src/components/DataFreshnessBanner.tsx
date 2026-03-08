import { useQuery } from '@tanstack/react-query'
import { apiFetch } from '../lib/api'

interface DataFreshness {
  status: string
  sources: Record<string, {
    last_seen: string | null
    age_minutes: number | null
    status: string
  }>
}

export function DataFreshnessBanner() {
  const { data } = useQuery({
    queryKey: ['data-freshness'],
    queryFn: () => apiFetch<DataFreshness>('/health/data-freshness'),
    refetchInterval: 60_000,
  })

  if (!data?.sources) return null

  const degraded = Object.entries(data.sources).filter(
    ([, s]) => s.status === 'stale' || s.status === 'offline'
  )

  if (degraded.length === 0) return null

  return (
    <div style={{
      background: 'rgba(234, 88, 12, 0.15)',
      border: '1px solid #ea580c',
      borderRadius: 'var(--radius)',
      padding: '8px 14px',
      marginBottom: 12,
      fontSize: 12,
      color: '#ea580c',
      display: 'flex',
      alignItems: 'center',
      gap: 8,
    }}>
      <span style={{ fontSize: 16 }}>&#9888;</span>
      <span>
        <b>Data feed degraded:</b>{' '}
        {degraded.map(([name, s]) => `${name} (${s.status})`).join(', ')}
      </span>
    </div>
  )
}
