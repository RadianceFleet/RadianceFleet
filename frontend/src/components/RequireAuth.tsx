import { useAuth } from '../hooks/useAuth'

interface RequireAuthProps {
  role?: 'authenticated' | 'senior_or_admin' | 'admin'
  fallback?: 'hidden' | 'access-denied'
  children: React.ReactNode
}

export function RequireAuth({ role = 'authenticated', fallback = 'access-denied', children }: RequireAuthProps) {
  const { isAuthenticated, isSeniorOrAdmin, isAdmin } = useAuth()

  let allowed = false
  if (role === 'authenticated') allowed = isAuthenticated
  else if (role === 'senior_or_admin') allowed = isSeniorOrAdmin
  else if (role === 'admin') allowed = isAdmin

  if (allowed) return <>{children}</>

  if (fallback === 'hidden') return null

  const message = !isAuthenticated
    ? 'You need to log in to access this page.'
    : 'You do not have permission to access this page.'

  return (
    <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-muted)' }}>
      <h3 style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>Access Denied</h3>
      <p style={{ fontSize: '0.875rem' }}>{message}</p>
    </div>
  )
}
