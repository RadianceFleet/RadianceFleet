interface ScoreBadgeProps { score: number; size?: 'sm' | 'md' }

export function ScoreBadge({ score, size = 'md' }: ScoreBadgeProps) {
  const color =
    score >= 76 ? 'var(--score-critical)' :
    score >= 51 ? 'var(--score-high)' :
    score >= 21 ? 'var(--score-medium)' :
    'var(--score-low)'
  const fontSize = size === 'sm' ? '0.75rem' : '0.875rem'
  const padding = size === 'sm' ? '0.125rem 0.375rem' : '0.25rem 0.5rem'
  return (
    <span style={{
      display: 'inline-block',
      background: color,
      color: 'white',
      padding,
      borderRadius: 'var(--radius)',
      fontSize,
      fontWeight: 700,
    }}>
      {score}
    </span>
  )
}
