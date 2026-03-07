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
import { DarkVesselsPage } from './pages/DarkVesselsPage'
import { MergeCandidatesPage } from './pages/MergeCandidatesPage'
import { DonatePage } from './pages/DonatePage'
import { FleetAnalysisPage } from './pages/FleetAnalysisPage'
import { OwnershipGraphPage } from './pages/OwnershipGraphPage'
import { DetectorResultsPage } from './pages/DetectorResultsPage'
import { VoyagePredictionPage } from './pages/VoyagePredictionPage'
import { HuntPage } from './pages/HuntPage'
import { TipsAdminPage } from './pages/TipsAdminPage'
import { VesselTimelinePage } from './pages/VesselTimelinePage'

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
            <Route path="vessels/:id/detectors" element={<DetectorResultsPage />} />
            <Route path="vessels/:id/voyage" element={<VoyagePredictionPage />} />
            <Route path="vessels/:id/timeline" element={<VesselTimelinePage />} />
            <Route path="map" element={<MapOverviewPage />} />
            <Route path="sts-events" element={<StsEventsPage />} />
            <Route path="corridors" element={<CorridorsPage />} />
            <Route path="corridors/:id" element={<CorridorDetailPage />} />
            <Route path="watchlist" element={<WatchlistPage />} />
            <Route path="fleet" element={<FleetAnalysisPage />} />
            <Route path="ownership" element={<OwnershipGraphPage />} />
            <Route path="ingest" element={<IngestionPage />} />
            <Route path="dark-vessels" element={<DarkVesselsPage />} />
            <Route path="merge-candidates" element={<MergeCandidatesPage />} />
            <Route path="hunt" element={<HuntPage />} />
            <Route path="detect" element={<DetectionPanel />} />
            <Route path="admin/tips" element={<TipsAdminPage />} />
            <Route path="donate" element={<DonatePage />} />
          </Route>
        </Routes>
      </ErrorBoundary>
    </BrowserRouter>
  )
}
