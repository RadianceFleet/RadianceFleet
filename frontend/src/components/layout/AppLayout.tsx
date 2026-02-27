import { NavLink, Outlet } from 'react-router-dom'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard' },
  { to: '/alerts', label: 'Alerts' },
  { to: '/vessels', label: 'Vessels' },
  { to: '/map', label: 'Map' },
  { to: '/sts-events', label: 'STS Events' },
  { to: '/dark-vessels', label: 'Dark Vessels' },
  { to: '/corridors', label: 'Corridors' },
  { to: '/watchlist', label: 'Watchlist' },
  { to: '/merge-candidates', label: 'Merges' },
  { to: '/ingest', label: 'Ingest' },
  { to: '/detect', label: 'Detect' },
]

export function AppLayout() {
  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <nav style={{
        width: 200,
        background: 'var(--bg-card)',
        borderRight: '1px solid var(--border)',
        padding: '1rem 0',
        flexShrink: 0,
      }}>
        <div style={{ padding: '0 1rem 1rem', fontWeight: 700, fontSize: '1rem', color: 'var(--accent)' }}>
          RadianceFleet
        </div>
        {NAV_ITEMS.map(item => (
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
        <div style={{
          position: 'fixed',
          bottom: 0,
          width: 200,
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
    </div>
  )
}

export default AppLayout
