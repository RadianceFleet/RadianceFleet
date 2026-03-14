import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { AlertListPage } from "./components/AlertList";
import { AlertDetail } from "./components/AlertDetail";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { AppLayout } from "./components/layout/AppLayout";
import { RequireAuth } from "./components/RequireAuth";
import { DashboardPage } from "./pages/DashboardPage";
import { VesselSearchPage } from "./pages/VesselSearchPage";
import { VesselDetailPage } from "./pages/VesselDetailPage";
import { MapOverviewPage } from "./pages/MapOverviewPage";
import { StsEventsPage } from "./pages/StsEventsPage";
import { CorridorsPage } from "./pages/CorridorsPage";
import { CorridorDetailPage } from "./pages/CorridorDetailPage";
import { WatchlistPage } from "./pages/WatchlistPage";
import { IngestionPage } from "./pages/IngestionPage";
import { DetectionPanel } from "./pages/DetectionPanel";
import { DarkVesselsPage } from "./pages/DarkVesselsPage";
import { MergeCandidatesPage } from "./pages/MergeCandidatesPage";
import { DonatePage } from "./pages/DonatePage";
import { FleetAnalysisPage } from "./pages/FleetAnalysisPage";
import { OwnershipGraphPage } from "./pages/OwnershipGraphPage";
import { DetectorResultsPage } from "./pages/DetectorResultsPage";
import { VoyagePredictionPage } from "./pages/VoyagePredictionPage";
import { HuntPage } from "./pages/HuntPage";
import { TipsAdminPage } from "./pages/TipsAdminPage";
import { VesselTimelinePage } from "./pages/VesselTimelinePage";
import { AccuracyDashboardPage } from "./pages/AccuracyDashboardPage";
import { DataHealthPage } from "./pages/DataHealthPage";
import { GlobalDetectionsPage } from "./pages/GlobalDetectionsPage";
import { EmbedVesselPage } from "./pages/EmbedVesselPage";
import { useAlertStream } from "./hooks/useAlertStream";
import { AlertToast } from "./components/AlertToast";
import { useAuth } from "./hooks/useAuth";

const OwnershipNetworkPage = lazy(() => import('./pages/OwnershipNetworkPage'));
const FPTuningPage = lazy(() => import('./pages/FPTuningPage'));
const PublicDashboardPage = lazy(() => import('./pages/PublicDashboardPage'));
const EmbedGeneratorPage = lazy(() => import('./pages/EmbedGeneratorPage'));
const TeamDashboardPage = lazy(() => import('./pages/TeamDashboardPage'));

export default function App() {
  const { isAuthenticated } = useAuth();
  const { lastAlert } = useAlertStream({ enabled: isAuthenticated });

  return (
    <BrowserRouter>
      <ErrorBoundary>
        <AlertToast alert={lastAlert} />
        <Routes>
          <Route path="/embed/vessel/:vesselId" element={<EmbedVesselPage />} />
          <Route path="public" element={<Suspense fallback={<div>Loading...</div>}><PublicDashboardPage /></Suspense>} />
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
            <Route
              path="ingest"
              element={
                <RequireAuth>
                  <IngestionPage />
                </RequireAuth>
              }
            />
            <Route path="detections" element={<GlobalDetectionsPage />} />
            <Route path="dark-vessels" element={<DarkVesselsPage />} />
            <Route path="merge-candidates" element={<MergeCandidatesPage />} />
            <Route path="hunt" element={<HuntPage />} />
            <Route path="detect" element={<DetectionPanel />} />
            <Route path="accuracy" element={<AccuracyDashboardPage />} />
            <Route path="data-health" element={<DataHealthPage />} />
            <Route
              path="admin/tips"
              element={
                <RequireAuth>
                  <TipsAdminPage />
                </RequireAuth>
              }
            />
            <Route path="donate" element={<DonatePage />} />
            <Route path="ownership-network" element={<Suspense fallback={<div>Loading...</div>}><OwnershipNetworkPage /></Suspense>} />
            <Route
              path="fp-tuning"
              element={
                <RequireAuth>
                  <Suspense fallback={<div>Loading...</div>}><FPTuningPage /></Suspense>
                </RequireAuth>
              }
            />
            <Route
              path="embed-generator"
              element={
                <RequireAuth>
                  <Suspense fallback={<div>Loading...</div>}><EmbedGeneratorPage /></Suspense>
                </RequireAuth>
              }
            />
            <Route
              path="team"
              element={
                <RequireAuth>
                  <Suspense fallback={<div>Loading...</div>}><TeamDashboardPage /></Suspense>
                </RequireAuth>
              }
            />
          </Route>
        </Routes>
      </ErrorBoundary>
    </BrowserRouter>
  );
}
