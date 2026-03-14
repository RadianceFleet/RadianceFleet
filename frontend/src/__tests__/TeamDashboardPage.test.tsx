import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "./testUtils";
import TeamDashboardPage from "../pages/TeamDashboardPage";

const mockFetch = vi.fn();
globalThis.fetch = mockFetch;

const WORKLOAD_DATA = [
  {
    analyst_id: 1,
    analyst_name: "Alice",
    open_alerts: 5,
    assigned_alerts: 8,
    utilization: 0.5,
    is_online: true,
    specializations: ["sanctions"],
  },
  {
    analyst_id: 2,
    analyst_name: "Bob",
    open_alerts: 2,
    assigned_alerts: 3,
    utilization: 0.2,
    is_online: false,
    specializations: [],
  },
];

const FEED_DATA = [
  {
    event_type: "handoff",
    analyst_name: "Alice",
    description: "Handed off alert #10 to Bob",
    timestamp: "2026-03-14T12:00:00",
    related_id: 10,
  },
];

const QUEUE_DATA = [
  {
    alert_id: 42,
    risk_score: 85,
    vessel_name: null,
    corridor_name: null,
    suggested_analyst_id: 2,
    suggested_analyst_name: "Bob",
  },
];

function mockApiSuccess() {
  mockFetch.mockImplementation((url: string) => {
    const urlStr = String(url);
    if (urlStr.includes("/workload/detailed")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(WORKLOAD_DATA) });
    }
    if (urlStr.includes("/activity-feed")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(FEED_DATA) });
    }
    if (urlStr.includes("/analysts/queue") || urlStr.endsWith("/queue")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(QUEUE_DATA) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
  });
}

function mockApiEmpty() {
  mockFetch.mockResolvedValue({
    ok: true,
    json: () => Promise.resolve([]),
  });
}

function mockApiError() {
  mockFetch.mockResolvedValue({
    ok: false,
    status: 500,
    statusText: "Internal Server Error",
    json: () => Promise.resolve({ detail: "Server error" }),
  });
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("TeamDashboardPage", () => {
  it("renders page title", async () => {
    mockApiEmpty();
    renderWithProviders(<TeamDashboardPage />, {
      auth: { role: "analyst", authenticated: true },
    });
    expect(screen.getByText("Team Dashboard")).toBeTruthy();
  });

  it("displays capacity bars section", async () => {
    mockApiSuccess();
    renderWithProviders(<TeamDashboardPage />, {
      auth: { role: "analyst", authenticated: true },
    });
    await waitFor(() => {
      expect(screen.getByText("Analyst Capacity")).toBeTruthy();
    });
  });

  it("shows online indicators for analysts", async () => {
    mockApiSuccess();
    renderWithProviders(<TeamDashboardPage />, {
      auth: { role: "analyst", authenticated: true },
    });
    await waitFor(() => {
      const indicators = screen.getAllByTestId("online-indicator");
      expect(indicators.length).toBe(2);
      expect(indicators[0].textContent).toContain("Alice");
      expect(indicators[1].textContent).toContain("Bob");
    });
  });

  it("displays queue table with alert data", async () => {
    mockApiSuccess();
    renderWithProviders(<TeamDashboardPage />, {
      auth: { role: "analyst", authenticated: true },
    });
    await waitFor(() => {
      expect(screen.getByText("Assignment Queue")).toBeTruthy();
      const table = screen.getByTestId("queue-table");
      expect(table).toBeTruthy();
      expect(table.textContent).toContain("42");
      expect(table.textContent).toContain("85");
    });
  });

  it("shows suggested analyst in queue", async () => {
    mockApiSuccess();
    renderWithProviders(<TeamDashboardPage />, {
      auth: { role: "analyst", authenticated: true },
    });
    await waitFor(() => {
      const table = screen.getByTestId("queue-table");
      expect(table).toBeTruthy();
      expect(table.textContent).toContain("Bob");
    });
  });

  it("shows activity feed", async () => {
    mockApiSuccess();
    renderWithProviders(<TeamDashboardPage />, {
      auth: { role: "analyst", authenticated: true },
    });
    await waitFor(() => {
      expect(screen.getByText("Recent Activity")).toBeTruthy();
      expect(screen.getByTestId("activity-feed")).toBeTruthy();
      expect(screen.getByText(/Handed off alert #10 to Bob/)).toBeTruthy();
    });
  });

  it("handles empty data gracefully", async () => {
    mockApiEmpty();
    renderWithProviders(<TeamDashboardPage />, {
      auth: { role: "analyst", authenticated: true },
    });
    await waitFor(() => {
      expect(screen.getByText("No analyst data available.")).toBeTruthy();
      expect(screen.getByText("No unassigned high-risk alerts.")).toBeTruthy();
      expect(screen.getByText("No recent activity.")).toBeTruthy();
    });
  });

  it("handles API error", async () => {
    mockApiError();
    renderWithProviders(<TeamDashboardPage />, {
      auth: { role: "analyst", authenticated: true },
    });
    await waitFor(() => {
      expect(screen.getByText("Failed to load workload data")).toBeTruthy();
    });
  });
});
