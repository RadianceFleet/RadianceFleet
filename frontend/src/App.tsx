import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AlertListPage } from './components/AlertList'
import { AlertDetail } from './components/AlertDetail'
import { ErrorBoundary } from './components/ErrorBoundary'
import { AppLayout } from './components/layout/AppLayout'
import { DashboardPage } from './pages/DashboardPage'
import { VesselSearchPage } from './pages/VesselSearchPage'
import { VesselDetailPage } from './pages/VesselDetailPage'
import { MapOverviewPage } from './pages/MapOverviewPage'
import { StsEventsPage } from './pages/StsEventsPage'
import { CorridorsPage } from './pages/CorridorsPage'
import { CorridorDetailPage } from './pages/CorridorDetailPage'
import { WatchlistPage } from './pages/WatchlistPage'
import { IngestionPage } from './pages/IngestionPage'
import { DetectionPanel } from './pages/DetectionPanel'

export default function App() {
  return (
    <BrowserRouter>
      <ErrorBoundary>
        <Routes>
          <Route element={<AppLayout />}>
            <Route index element={<DashboardPage />} />
            <Route path="alerts" element={<AlertListPage />} />
            <Route path="alerts/:id" element={<AlertDetail />} />
            <Route path="vessels" element={<VesselSearchPage />} />
            <Route path="vessels/:id" element={<VesselDetailPage />} />
            <Route path="map" element={<MapOverviewPage />} />
            <Route path="sts-events" element={<StsEventsPage />} />
            <Route path="corridors" element={<CorridorsPage />} />
            <Route path="corridors/:id" element={<CorridorDetailPage />} />
            <Route path="watchlist" element={<WatchlistPage />} />
            <Route path="ingest" element={<IngestionPage />} />
            <Route path="detect" element={<DetectionPanel />} />
          </Route>
        </Routes>
      </ErrorBoundary>
    </BrowserRouter>
  )
}
