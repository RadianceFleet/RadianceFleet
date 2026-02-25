import { Link } from 'react-router-dom'
import { useStats } from '../hooks/useStats'
import { Card } from '../components/ui/Card'
import { Spinner } from '../components/ui/Spinner'

export function DashboardPage() {
  const { data: stats, isLoading, error } = useStats()

  if (isLoading) return <Spinner text="Loading dashboard…" />
  if (error || !stats) return <p style={{ color: 'var(--score-critical)' }}>Failed to load stats.</p>

  const { alert_counts: counts, by_status, vessels_with_multiple_gaps_7d } = stats
  const total = counts.total || 1 // avoid division by zero

  const statCards = [
    { label: 'Total Alerts', value: counts.total, color: 'var(--text-bright)' },
    { label: 'Critical (76+)', value: counts.critical, color: 'var(--score-critical)' },
    { label: 'Vessels Tracked', value: stats.distinct_vessels ?? 0, color: 'var(--accent)' },
    { label: 'Multi-gap Vessels (7d)', value: vessels_with_multiple_gaps_7d, color: 'var(--warning)' },
  ]

  const scoreBands = [
    { label: 'Critical', count: counts.critical, color: 'var(--score-critical)' },
    { label: 'High', count: counts.high, color: 'var(--score-high)' },
    { label: 'Medium', count: counts.medium, color: 'var(--score-medium)' },
    { label: 'Low', count: counts.low, color: 'var(--score-low)' },
  ]

  return (
    <div>
      <h2 style={{ margin: '0 0 1rem', fontSize: '1rem', color: 'var(--text-muted)' }}>Dashboard</h2>

      {/* Stat cards grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.75rem', marginBottom: '1.5rem' }}>
        {statCards.map(s => (
          <Card key={s.label}>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '0.25rem' }}>{s.label}</div>
            <div style={{ fontSize: '1.75rem', fontWeight: 700, color: s.color }}>{s.value}</div>
          </Card>
        ))}
      </div>

      {/* Score distribution */}
      <Card style={{ marginBottom: '1rem' }}>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Score Distribution
        </h3>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          {scoreBands.map(b => {
            const pct = total > 0 ? (b.count / total) * 100 : 0
            return (
              <div key={b.label}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', marginBottom: '0.25rem' }}>
                  <span style={{ color: b.color }}>{b.label}</span>
                  <span style={{ color: 'var(--text-muted)' }}>{b.count} ({pct.toFixed(0)}%)</span>
                </div>
                <div style={{ height: 6, background: 'var(--bg-base)', borderRadius: 3 }}>
                  <div style={{ height: '100%', width: `${pct}%`, background: b.color, borderRadius: 3 }} />
                </div>
              </div>
            )
          })}
        </div>
      </Card>

      {/* Status breakdown */}
      <Card style={{ marginBottom: '1rem' }}>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Status Breakdown
        </h3>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem' }}>
          {Object.entries(by_status).map(([status, count]) => (
            <div key={status} style={{ minWidth: 120 }}>
              <div style={{ fontSize: '1.25rem', fontWeight: 700, color: 'var(--text-bright)' }}>{count}</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{status.replace(/_/g, ' ')}</div>
            </div>
          ))}
        </div>
      </Card>

      <Link
        to="/alerts"
        style={{
          display: 'inline-block',
          padding: '0.5rem 1rem',
          background: 'var(--accent-primary)',
          color: 'white',
          borderRadius: 'var(--radius)',
          fontSize: '0.875rem',
        }}
      >
        View All Alerts
      </Link>
    </div>
  )
}
