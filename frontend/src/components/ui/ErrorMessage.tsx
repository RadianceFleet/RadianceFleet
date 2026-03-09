import { ApiError } from '../../lib/api'

function statusLabel(status: number): string {
  switch (status) {
    case 401: return 'Authentication required'
    case 403: return 'Access denied'
    case 404: return 'Not found'
    case 422: return 'Validation error'
    default: return status >= 500 ? 'Server error' : `Error ${status}`
  }
}

export function ErrorMessage({
  error,
  subject,
  onRetry,
}: {
  error: Error | null
  subject?: string
  onRetry?: () => void
}) {
  if (!error) return null

  const isApi = error instanceof ApiError
  const status = isApi ? (error as ApiError).status : undefined
  const detail = error.message || 'Unknown error'

  return (
    <div
      style={{
        padding: '0.75rem 1rem',
        background: 'rgba(220, 38, 38, 0.08)',
        border: '1px solid var(--score-critical)',
        borderRadius: 'var(--radius)',
        fontSize: '0.8125rem',
        color: 'var(--score-critical)',
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: status || onRetry ? 4 : 0 }}>
        {subject ? `Failed to load ${subject}` : 'Something went wrong'}
        {status ? ` — ${statusLabel(status)}` : ''}
      </div>
      {detail && (
        <div style={{ color: 'var(--text-muted)', fontSize: '0.75rem' }}>
          {detail}
        </div>
      )}
      {onRetry && (
        <button
          onClick={onRetry}
          style={{
            marginTop: 8,
            padding: '4px 12px',
            fontSize: '0.75rem',
            background: 'var(--score-critical)',
            color: '#fff',
            border: 'none',
            borderRadius: 'var(--radius)',
            cursor: 'pointer',
          }}
        >
          Retry
        </button>
      )}
    </div>
  )
}
