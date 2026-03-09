import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../../hooks/useAuth'
import { LoginModal } from '../LoginModal'

interface NavItem {
  to: string
  label: string
  requiredRole?: 'authenticated'
}

const NAV_ITEMS: NavItem[] = [
  { to: '/', label: 'Dashboard' },
  { to: '/alerts', label: 'Alerts' },
  { to: '/vessels', label: 'Vessels' },
  { to: '/map', label: 'Map' },
  { to: '/sts-events', label: 'STS Events' },
  { to: '/dark-vessels', label: 'Dark Vessels' },
  { to: '/detections', label: 'Detections' },
  { to: '/corridors', label: 'Corridors' },
  { to: '/watchlist', label: 'Watchlist' },
  { to: '/fleet', label: 'Fleet' },
  { to: '/ownership', label: 'Ownership' },
  { to: '/merge-candidates', label: 'Merges' },
  { to: '/hunt', label: 'Hunt' },
  { to: '/ingest', label: 'Ingest', requiredRole: 'authenticated' },
  { to: '/accuracy', label: 'Accuracy' },
  { to: '/detect', label: 'Detect' },
  { to: '/admin/tips', label: 'Tips', requiredRole: 'authenticated' },
  { to: '/donate', label: 'Support' },
]

export function AppLayout() {
  const { isAuthenticated, analyst, logout } = useAuth()
  const [showLogin, setShowLogin] = useState(false)

  const visibleItems = NAV_ITEMS.filter(item =>
    !item.requiredRole || isAuthenticated
  )

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <nav style={{
        width: 200,
        background: 'var(--bg-card)',
        borderRight: '1px solid var(--border)',
        padding: '1rem 0',
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
      }}>
        <div style={{ padding: '0 1rem 1rem', fontWeight: 700, fontSize: '1rem', color: 'var(--accent)' }}>
          RadianceFleet
        </div>
        <div style={{ flex: 1 }}>
          {visibleItems.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              style={({ isActive }) => ({
                display: 'block',
                padding: '0.5rem 1rem',
                color: isActive ? 'var(--accent)' : 'var(--text-muted)',
                background: isActive ? 'rgba(96, 165, 250, 0.1)' : 'transparent',
                textDecoration: 'none',
                fontSize: '0.875rem',
                borderLeft: isActive ? '3px solid var(--accent)' : '3px solid transparent',
              })}
            >
              {item.label}
            </NavLink>
          ))}
        </div>
        <div style={{
          padding: '0.5rem 1rem',
          borderTop: '1px solid var(--border)',
        }}>
          {isAuthenticated ? (
            <div style={{ fontSize: '0.75rem' }}>
              <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>
                {analyst?.display_name ?? analyst?.username ?? 'Analyst'}
              </div>
              <button
                onClick={logout}
                style={{
                  background: 'none', border: '1px solid var(--border)',
                  borderRadius: 'var(--radius)', padding: '3px 10px',
                  color: 'var(--text-muted)', cursor: 'pointer', fontSize: '0.75rem',
                }}
              >
                Log out
              </button>
            </div>
          ) : (
            <button
              onClick={() => setShowLogin(true)}
              style={{
                background: 'var(--accent)', border: 'none',
                borderRadius: 'var(--radius)', padding: '4px 12px',
                color: '#fff', cursor: 'pointer', fontSize: '0.75rem', fontWeight: 600,
              }}
            >
              Log in
            </button>
          )}
        </div>
        <div style={{
          padding: '0.5rem',
          fontSize: '0.625rem',
          color: 'var(--text-dim)',
          borderTop: '1px solid var(--border)',
          background: 'var(--bg-card)',
        }}>
          DISCLAIMER: Investigative triage only. Not a legal determination.
        </div>
      </nav>
      <main style={{ flex: 1, padding: '1.5rem', overflow: 'auto' }}>
        <Outlet />
      </main>
      {showLogin && (
        <LoginModal
          onSuccess={() => setShowLogin(false)}
          onClose={() => setShowLogin(false)}
        />
      )}
    </div>
  )
}

export default AppLayout
