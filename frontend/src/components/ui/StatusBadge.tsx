const STATUS_COLORS: Record<string, string> = {
  new: 'var(--accent)',
  under_review: 'var(--warning)',
  needs_satellite_check: '#8b5cf6',
  documented: 'var(--score-low)',
  dismissed: 'var(--text-dim)',
}

export function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? 'var(--text-muted)'
  return (
    <span style={{
      display: 'inline-block',
      padding: '0.125rem 0.5rem',
      border: `1px solid ${color}`,
      color,
      borderRadius: '999px',
      fontSize: '0.75rem',
      textTransform: 'uppercase',
      letterSpacing: '0.05em',
    }}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}
