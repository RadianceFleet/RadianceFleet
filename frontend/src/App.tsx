import { BrowserRouter, Routes, Route, Link } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { AlertList, type Filters } from './components/AlertList'
import { AlertDetail } from './components/AlertDetail'

const queryClient = new QueryClient()

const inputStyle: React.CSSProperties = {
  background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155',
  padding: '6px 10px', borderRadius: 4, fontSize: 13,
}

function AlertListPage() {
  const [filters, setFilters] = useState<Filters>({ min_score: '', status: '', vessel_name: '' })
  return (
    <div>
      <h2 style={{ margin: '0 0 16px', fontSize: 16, color: '#94a3b8' }}>Alert Queue</h2>
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
        <input
          placeholder="Min score"
          value={filters.min_score}
          onChange={e => setFilters(f => ({ ...f, min_score: e.target.value }))}
          style={{ ...inputStyle, width: 90 }}
        />
        <select
          value={filters.status}
          onChange={e => setFilters(f => ({ ...f, status: e.target.value }))}
          style={inputStyle}
        >
          <option value="">All statuses</option>
          <option value="new">New</option>
          <option value="under_review">Under review</option>
          <option value="needs_satellite_check">Needs satellite check</option>
          <option value="documented">Documented</option>
          <option value="dismissed">Dismissed</option>
        </select>
        <input
          placeholder="Vessel name"
          value={filters.vessel_name}
          onChange={e => setFilters(f => ({ ...f, vessel_name: e.target.value }))}
          style={{ ...inputStyle, width: 160 }}
        />
      </div>
      <AlertList filters={filters} />
    </div>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div style={{ fontFamily: 'monospace', background: '#0f172a', color: '#e2e8f0', minHeight: '100vh', padding: 24 }}>
          <header style={{ marginBottom: 24 }}>
            <Link to="/" style={{ color: '#e2e8f0', textDecoration: 'none' }}>
              <h1 style={{ margin: 0, fontSize: 20, letterSpacing: 1 }}>RadianceFleet</h1>
            </Link>
            <p style={{ color: '#f59e0b', fontSize: 12, margin: '4px 0 0' }}>
              ⚠ Investigative triage — not a legal determination. Verify independently before publishing.
            </p>
          </header>
          <Routes>
            <Route path="/" element={<AlertListPage />} />
            <Route path="/alerts/:id" element={<AlertDetail />} />
          </Routes>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
