export function EmptyState({ title, description }: { title: string; description?: string }) {
  return (
    <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-muted)' }}>
      <p style={{ fontSize: '1.125rem', margin: '0 0 0.5rem' }}>{title}</p>
      {description && <p style={{ fontSize: '0.875rem', margin: 0 }}>{description}</p>}
    </div>
  )
}
