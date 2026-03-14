import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "./testUtils";

// Mock recharts to avoid canvas issues in jsdom
vi.mock("recharts", () => {
  const Noop = ({ children }: { children?: React.ReactNode }) => <>{children}</>;
  return {
    ResponsiveContainer: Noop,
    LineChart: Noop,
    Line: Noop,
    BarChart: Noop,
    Bar: Noop,
    Cell: Noop,
    XAxis: Noop,
    YAxis: Noop,
    Tooltip: Noop,
    PieChart: Noop,
    Pie: Noop,
    Legend: Noop,
  };
});

const MOCK_DASHBOARD = {
  vessel_count: 142,
  alert_counts: { high: 12, medium: 30, low: 58 },
  detection_coverage: { monitored_zones: 8, active_corridors: 8 },
  recent_alerts: [
    { mmsi_suffix: "6789", flag: "PA", tier: "high" as const, created_at: "2026-03-14T10:00:00" },
    { mmsi_suffix: "1234", flag: "LR", tier: "medium" as const, created_at: "2026-03-13T08:00:00" },
  ],
  trend_buckets: [
    { date: "2026-03-12", count: 5 },
    { date: "2026-03-13", count: 8 },
  ],
  detections_by_type: { gap: 80, spoofing: 15, sts: 5 },
};

const MOCK_TRENDS = {
  days: [
    { date: "2026-01-01", count: 3 },
    { date: "2026-01-02", count: 7 },
  ],
};

vi.mock("../hooks/usePublicDashboard", () => ({
  usePublicDashboard: () => ({
    data: MOCK_DASHBOARD,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  usePublicTrends: () => ({
    data: MOCK_TRENDS,
    isLoading: false,
    error: null,
  }),
}));

import PublicDashboardPage from "../pages/PublicDashboardPage";

describe("PublicDashboardPage", () => {
  it("renders stat cards with vessel count", () => {
    renderWithProviders(<PublicDashboardPage />);
    expect(screen.getByText("142")).toBeInTheDocument();
    expect(screen.getByText("Vessels Monitored")).toBeInTheDocument();
  });

  it("renders high-risk alert count", () => {
    renderWithProviders(<PublicDashboardPage />);
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("High-Risk Alerts")).toBeInTheDocument();
  });

  it("renders monitored zones count", () => {
    renderWithProviders(<PublicDashboardPage />);
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("Monitored Zones")).toBeInTheDocument();
  });

  it("renders recent alerts table with anonymised MMSI suffix", () => {
    renderWithProviders(<PublicDashboardPage />);
    expect(screen.getByText("...6789")).toBeInTheDocument();
    expect(screen.getByText("...1234")).toBeInTheDocument();
  });

  it("shows flag state in recent alerts", () => {
    renderWithProviders(<PublicDashboardPage />);
    expect(screen.getByText("PA")).toBeInTheDocument();
    expect(screen.getByText("LR")).toBeInTheDocument();
  });

  it("does not expose full MMSI in the page", () => {
    renderWithProviders(<PublicDashboardPage />);
    // No 9-digit MMSI should appear anywhere
    const html = document.body.innerHTML;
    expect(html).not.toMatch(/\d{9}/);
  });

  it("renders the page heading", () => {
    renderWithProviders(<PublicDashboardPage />);
    expect(screen.getByText("Public Dashboard")).toBeInTheDocument();
  });

  it("renders detections by type section", () => {
    renderWithProviders(<PublicDashboardPage />);
    expect(screen.getByText("Detections by Type")).toBeInTheDocument();
  });
});

describe("PublicDashboardPage additional checks", () => {
  it("renders the auto-refresh description text", () => {
    renderWithProviders(<PublicDashboardPage />);
    expect(screen.getByText(/refreshes every 5 minutes/)).toBeInTheDocument();
  });

  it("renders recent alerts heading", () => {
    renderWithProviders(<PublicDashboardPage />);
    expect(screen.getByText(/Recent Alerts/)).toBeInTheDocument();
  });
});
