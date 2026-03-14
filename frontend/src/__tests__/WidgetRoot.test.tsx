import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";

// Mock react-leaflet to avoid JSDOM issues
vi.mock("react-leaflet", () => ({
  MapContainer: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="mock-map">{children}</div>
  ),
  TileLayer: () => <div />,
  Marker: () => <div data-testid="mock-marker" />,
}));

vi.mock("leaflet", () => ({
  default: { icon: () => ({}) },
  icon: () => ({}),
}));

const mockFetch = vi.fn();
globalThis.fetch = mockFetch;

function setSearchParams(params: Record<string, string>) {
  const search = new URLSearchParams(params).toString();
  Object.defineProperty(window, "location", {
    value: { ...window.location, search: `?${search}` },
    writable: true,
  });
}

function renderWidget() {
  // Import fresh each time to pick up new location.search
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  // Dynamic import workaround — just require directly
  const WidgetRoot =
    require("../embed/WidgetRoot").default;
  return render(
    <QueryClientProvider client={queryClient}>
      <WidgetRoot />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("WidgetRoot", () => {
  it("shows error when vessel param is missing", () => {
    setSearchParams({ type: "summary", apiKey: "test" });
    renderWidget();
    expect(screen.getByText("Missing vessel parameter")).toBeTruthy();
  });

  it("shows error when apiKey param is missing", () => {
    setSearchParams({ type: "summary", vessel: "1" });
    renderWidget();
    expect(screen.getByText("Missing apiKey parameter")).toBeTruthy();
  });

  it("renders summary widget on successful fetch", async () => {
    setSearchParams({ type: "summary", vessel: "1", apiKey: "test-key" });
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          vessel_id: 1,
          name: "TEST VESSEL",
          mmsi: "123456789",
          imo: "1234567",
          flag: "PA",
          risk_score: 65,
          risk_tier: "high",
          on_watchlist: false,
        }),
    });

    renderWidget();
    await waitFor(() => {
      expect(screen.getByText("TEST VESSEL")).toBeTruthy();
    });
    expect(screen.getByText("HIGH")).toBeTruthy();
  });

  it("renders timeline widget for type=timeline", async () => {
    setSearchParams({ type: "timeline", vessel: "1", apiKey: "test-key" });
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          vessel_id: 1,
          items: [],
          count: 0,
        }),
    });

    renderWidget();
    await waitFor(() => {
      expect(screen.getByText("No alerts in the last 30 days.")).toBeTruthy();
    });
  });

  it("shows error message on fetch failure", async () => {
    setSearchParams({ type: "summary", vessel: "1", apiKey: "test-key" });
    mockFetch.mockResolvedValueOnce({ ok: false, status: 500 });

    renderWidget();
    await waitFor(() => {
      expect(screen.getByText("Failed to load widget data")).toBeTruthy();
    });
  });
});
