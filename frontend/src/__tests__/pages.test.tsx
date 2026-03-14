import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "./testUtils";

// Mock all hooks that fetch data so pages render without API calls
vi.mock("../hooks/useStats", () => ({
  useStats: () => ({
    data: {
      alert_counts: { total: 10, critical: 2, high: 3, medium: 3, low: 2 },
      by_status: { new: 5, documented: 3, dismissed: 2 },
      vessels_with_multiple_gaps_7d: 1,
      distinct_vessels: 42,
    },
    isLoading: false,
    error: null,
  }),
}));

vi.mock("../hooks/useAlerts", () => ({
  useAlerts: () => ({ data: { items: [], total: 0 }, isLoading: false, error: null }),
  useBulkUpdateAlertStatus: () => ({ mutate: vi.fn(), isPending: false }),
  useAlertMapPoints: () => ({ data: [], isLoading: false, error: null }),
}));

vi.mock("../hooks/useVessels", () => ({
  useVesselSearch: () => ({ data: { items: [] }, isLoading: false, error: null }),
  useMergeCandidates: () => ({ data: [], isLoading: false, error: null }),
  useMergeChains: () => ({ data: null, isLoading: false, error: null }),
}));

vi.mock("../hooks/useCorridors", () => ({
  useCorridors: () => ({ data: { items: [], total: 0 }, isLoading: false, error: null }),
  useCorridorGeoJSON: () => ({ data: null, isLoading: false, error: null }),
}));

vi.mock("../hooks/useWatchlist", () => ({
  useWatchlist: () => ({ data: { items: [], total: 0 }, isLoading: false, error: null }),
  useImportWatchlist: () => ({
    mutate: vi.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
  }),
  useRemoveWatchlistEntry: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../hooks/useStsEvents", () => ({
  useStsEvents: () => ({ data: { items: [], total: 0 }, isLoading: false, error: null }),
}));

vi.mock("../hooks/useStsValidation", () => ({
  useStsValidation: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../hooks/useValidation", () => ({
  useValidation: () => ({ data: null, isLoading: false, error: null }),
  useValidationSignals: () => ({ data: null, isLoading: false, error: null }),
  useValidationSweep: () => ({ data: null, isLoading: false, error: null }),
  useAnalystMetrics: () => ({ data: null, isLoading: false, error: null }),
  useDetectorCorrelation: () => ({ data: null, isLoading: false, error: null }),
  useLiveSignalEffectiveness: () => ({ data: null, isLoading: false, error: null }),
}));

vi.mock("../hooks/useGlobalDetections", () => ({
  useGlobalSpoofing: () => ({ data: { items: [], total: 0 }, isLoading: false, error: null }),
}));

vi.mock("../hooks/useStsChains", () => ({
  useStsChains: () => ({ data: { items: [], total: 0 }, isLoading: false, error: null }),
}));

vi.mock("../hooks/useLoitering", () => ({
  useGlobalLoitering: () => ({ data: { items: [], total: 0 }, isLoading: false, error: null }),
}));

vi.mock("../hooks/useDarkVessels", () => ({
  useDarkVessels: () => ({ data: { items: [], total: 0 }, isLoading: false, error: null }),
}));

vi.mock("../hooks/useIngestion", () => ({
  useImportAIS: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false }),
  useIngestionStatus: () => ({ data: null, isLoading: false }),
}));

vi.mock("../hooks/useFleet", () => ({
  useFleetAlerts: () => ({ data: { items: [], total: 0 }, isLoading: false, error: null }),
  useFleetClusters: () => ({ data: [], isLoading: false, error: null }),
  useFleetClusterDetail: () => ({ data: null, isLoading: false, error: null }),
}));

vi.mock("../hooks/useDetectors", () => ({
  useDetectorResults: () => ({ data: null, isLoading: false, error: null }),
}));

vi.mock("../hooks/useVoyagePrediction", () => ({
  useVoyagePrediction: () => ({ data: null, isLoading: false, error: null }),
}));

// Mock leaflet to avoid canvas/DOM issues in jsdom
vi.mock("react-leaflet", () => {
  const Noop = ({ children }: { children?: React.ReactNode }) => <>{children}</>;
  return {
    MapContainer: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="map">{children}</div>
    ),
    TileLayer: Noop,
    Marker: Noop,
    Popup: Noop,
    Circle: Noop,
    Polygon: Noop,
    GeoJSON: Noop,
    LayersControl: Object.assign(
      ({ children }: { children?: React.ReactNode }) => <>{children}</>,
      {
        BaseLayer: Noop,
        Overlay: Noop,
      }
    ),
    useMap: () => ({}),
  };
});

vi.mock("leaflet", () => ({
  default: {
    icon: () => ({}),
    divIcon: () => ({}),
    latLngBounds: () => ({}),
  },
  icon: () => ({}),
  divIcon: () => ({}),
  latLngBounds: () => ({}),
}));

vi.mock("leaflet.heat", () => ({}));

import { DashboardPage } from "../pages/DashboardPage";
import { VesselSearchPage } from "../pages/VesselSearchPage";
import { CorridorsPage } from "../pages/CorridorsPage";
import { WatchlistPage } from "../pages/WatchlistPage";
import { StsEventsPage } from "../pages/StsEventsPage";
import { DarkVesselsPage } from "../pages/DarkVesselsPage";
import { IngestionPage } from "../pages/IngestionPage";
import { MergeCandidatesPage } from "../pages/MergeCandidatesPage";
import { DetectionPanel } from "../pages/DetectionPanel";
import { DonatePage } from "../pages/DonatePage";
import { FleetAnalysisPage } from "../pages/FleetAnalysisPage";
import { OwnershipGraphPage } from "../pages/OwnershipGraphPage";
import { MapOverviewPage } from "../pages/MapOverviewPage";
import { AccuracyDashboardPage } from "../pages/AccuracyDashboardPage";
import { GlobalDetectionsPage } from "../pages/GlobalDetectionsPage";
import { AlertListPage } from "../components/AlertList";

describe("Page smoke tests", () => {
  it("DashboardPage renders stats", () => {
    renderWithProviders(<DashboardPage />);
    expect(screen.getByText("Dashboard")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument(); // total alerts
    expect(screen.getByText("View All Alerts")).toBeInTheDocument();
  });

  it("AlertListPage renders heading", () => {
    renderWithProviders(<AlertListPage />);
    expect(screen.getByText("Alert Queue")).toBeInTheDocument();
  });

  it("VesselSearchPage renders search input", () => {
    renderWithProviders(<VesselSearchPage />);
    expect(screen.getByText("Vessel Search")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Search MMSI/)).toBeInTheDocument();
  });

  it("CorridorsPage renders heading and create button when authenticated", () => {
    renderWithProviders(<CorridorsPage />, { auth: { role: "admin" } });
    expect(screen.getByText(/Corridors/)).toBeInTheDocument();
    expect(screen.getByText("Create Corridor")).toBeInTheDocument();
  });

  it("CorridorsPage hides create button when unauthenticated", () => {
    renderWithProviders(<CorridorsPage />);
    expect(screen.getByText(/Corridors/)).toBeInTheDocument();
    expect(screen.queryByText("Create Corridor")).not.toBeInTheDocument();
  });

  it("WatchlistPage renders", () => {
    renderWithProviders(<WatchlistPage />);
    expect(screen.getByText("Watchlist Management")).toBeInTheDocument();
  });

  it("StsEventsPage renders", () => {
    renderWithProviders(<StsEventsPage />);
    expect(screen.getByText("STS Transfer Events")).toBeInTheDocument();
  });

  it("DarkVesselsPage renders", () => {
    renderWithProviders(<DarkVesselsPage />);
    expect(screen.getByText(/Dark Vessel/)).toBeInTheDocument();
  });

  it("IngestionPage renders", () => {
    renderWithProviders(<IngestionPage />);
    expect(screen.getByText(/AIS Data/i)).toBeInTheDocument();
  });

  it("MergeCandidatesPage renders", () => {
    renderWithProviders(<MergeCandidatesPage />);
    expect(screen.getByText(/Merge/)).toBeInTheDocument();
  });

  it("DetectionPanel renders", () => {
    renderWithProviders(<DetectionPanel />);
    expect(screen.getByText("Detection Panel")).toBeInTheDocument();
  });

  it("DonatePage renders", () => {
    renderWithProviders(<DonatePage />);
    expect(screen.getByText(/Support RadianceFleet/)).toBeInTheDocument();
  });

  it("FleetAnalysisPage renders", () => {
    renderWithProviders(<FleetAnalysisPage />);
    expect(screen.getByText("Fleet Alerts")).toBeInTheDocument();
  });

  it("OwnershipGraphPage renders", () => {
    renderWithProviders(<OwnershipGraphPage />);
    expect(screen.getByText("Ownership Graph")).toBeInTheDocument();
  });

  it("MapOverviewPage renders", () => {
    renderWithProviders(<MapOverviewPage />);
    expect(screen.getByTestId("map")).toBeInTheDocument();
  });

  it("AccuracyDashboardPage renders", () => {
    renderWithProviders(<AccuracyDashboardPage />);
    expect(screen.getByText(/Accuracy/i)).toBeInTheDocument();
  });

  it("GlobalDetectionsPage renders", () => {
    renderWithProviders(<GlobalDetectionsPage />);
    expect(screen.getByText(/Detections/i)).toBeInTheDocument();
  });
});
