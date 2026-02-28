interface PaginationProps {
  page: number
  totalPages: number
  total: number
  onPageChange: (page: number) => void
  label?: string
}

export function Pagination({ page, totalPages, total, onPageChange, label = 'items' }: PaginationProps) {
  const btnStyle: React.CSSProperties = {
    padding: '6px 14px',
    background: 'var(--bg-card)',
    color: 'var(--text-muted)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    cursor: 'pointer',
    fontSize: '0.8125rem',
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: '0.75rem' }}>
      <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
        {total} {label}
      </span>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <button
          onClick={() => onPageChange(Math.max(0, page - 1))}
          disabled={page === 0}
          style={{ ...btnStyle, opacity: page === 0 ? 0.4 : 1 }}
        >
          Prev
        </button>
        <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>
          Page {page + 1} of {totalPages}
        </span>
        <button
          onClick={() => onPageChange(Math.min(totalPages - 1, page + 1))}
          disabled={page >= totalPages - 1}
          style={{ ...btnStyle, opacity: page >= totalPages - 1 ? 0.4 : 1 }}
        >
          Next
        </button>
      </div>
    </div>
  )
}
