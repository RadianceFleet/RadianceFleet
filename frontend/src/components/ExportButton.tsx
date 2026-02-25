export function ExportButton({ filters }: { filters: Record<string, string | undefined> }) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([k, v]) => { if (v) params.set(k, v) })
  return (
    <a
      href={`/api/v1/alerts/export?${params}`}
      download
      style={{
        display: 'inline-block',
        padding: '0.5rem 1rem',
        background: 'var(--accent-primary)',
        color: 'white',
        borderRadius: 'var(--radius)',
        fontSize: '0.875rem',
        textDecoration: 'none',
      }}
    >
      Export CSV
    </a>
  )
}
