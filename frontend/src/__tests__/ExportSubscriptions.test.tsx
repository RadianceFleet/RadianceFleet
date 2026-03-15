import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "./testUtils";
import ExportSubscriptionsPage from "../components/ExportSubscriptionsPage";

const mockFetch = vi.fn();
globalThis.fetch = mockFetch;

const SUBSCRIPTIONS_DATA = {
  total: 2,
  subscriptions: [
    {
      subscription_id: 1,
      name: "Daily Alerts Export",
      created_by: 1,
      schedule: "daily",
      schedule_day: null,
      schedule_hour_utc: 6,
      export_type: "alerts",
      filter_json: { date_mode: "last_day" },
      columns_json: null,
      format: "csv",
      delivery_method: "email",
      delivery_config_json: { email: "test@example.com" },
      is_active: true,
      last_run_at: "2026-03-15T06:00:00",
      last_run_status: "completed",
      last_run_rows: 42,
      created_at: "2026-03-01T00:00:00",
    },
    {
      subscription_id: 2,
      name: "Weekly Vessels",
      created_by: 1,
      schedule: "weekly",
      schedule_day: 0,
      schedule_hour_utc: 8,
      export_type: "vessels",
      filter_json: null,
      columns_json: ["vessel_id", "name", "mmsi"],
      format: "json",
      delivery_method: "s3",
      delivery_config_json: { bucket: "my-bucket" },
      is_active: false,
      last_run_at: null,
      last_run_status: null,
      last_run_rows: null,
      created_at: "2026-03-01T00:00:00",
    },
  ],
};

function mockFetchSuccess(data: unknown = SUBSCRIPTIONS_DATA) {
  mockFetch.mockResolvedValue({
    ok: true,
    json: async () => data,
  });
}

describe("ExportSubscriptionsPage", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("renders loading state", () => {
    mockFetch.mockReturnValue(new Promise(() => {}));
    renderWithProviders(<ExportSubscriptionsPage />, {
      auth: { role: "admin", authenticated: true },
    });
    expect(screen.getByText("Loading export subscriptions...")).toBeDefined();
  });

  it("renders subscriptions table", async () => {
    mockFetchSuccess();
    renderWithProviders(<ExportSubscriptionsPage />, {
      auth: { role: "admin", authenticated: true },
    });
    await waitFor(() => {
      expect(screen.getByText("Daily Alerts Export")).toBeDefined();
      expect(screen.getByText("Weekly Vessels")).toBeDefined();
    });
  });

  it("shows active/inactive status", async () => {
    mockFetchSuccess();
    renderWithProviders(<ExportSubscriptionsPage />, {
      auth: { role: "admin", authenticated: true },
    });
    await waitFor(() => {
      expect(screen.getByText("Active")).toBeDefined();
      expect(screen.getByText("Inactive")).toBeDefined();
    });
  });

  it("shows new subscription button", async () => {
    mockFetchSuccess();
    renderWithProviders(<ExportSubscriptionsPage />, {
      auth: { role: "admin", authenticated: true },
    });
    await waitFor(() => {
      expect(screen.getByText("+ New Subscription")).toBeDefined();
    });
  });

  it("renders empty state when no subscriptions", async () => {
    mockFetchSuccess({ total: 0, subscriptions: [] });
    renderWithProviders(<ExportSubscriptionsPage />, {
      auth: { role: "admin", authenticated: true },
    });
    await waitFor(() => {
      expect(screen.getByText("No export subscriptions configured.")).toBeDefined();
    });
  });

  it("renders error state", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "Server error" }),
      statusText: "Internal Server Error",
    });
    renderWithProviders(<ExportSubscriptionsPage />, {
      auth: { role: "admin", authenticated: true },
    });
    await waitFor(() => {
      expect(screen.getByText(/Error loading subscriptions/)).toBeDefined();
    });
  });

  it("shows schedule info for each subscription", async () => {
    mockFetchSuccess();
    renderWithProviders(<ExportSubscriptionsPage />, {
      auth: { role: "admin", authenticated: true },
    });
    await waitFor(() => {
      expect(screen.getByText("daily")).toBeDefined();
      expect(screen.getByText("weekly")).toBeDefined();
    });
  });

  it("shows export type", async () => {
    mockFetchSuccess();
    renderWithProviders(<ExportSubscriptionsPage />, {
      auth: { role: "admin", authenticated: true },
    });
    await waitFor(() => {
      expect(screen.getByText("alerts")).toBeDefined();
      expect(screen.getByText("vessels")).toBeDefined();
    });
  });

  it("has run/history/delete buttons", async () => {
    mockFetchSuccess();
    renderWithProviders(<ExportSubscriptionsPage />, {
      auth: { role: "admin", authenticated: true },
    });
    await waitFor(() => {
      const runButtons = screen.getAllByText("Run");
      expect(runButtons.length).toBe(2);
      const historyButtons = screen.getAllByText("History");
      expect(historyButtons.length).toBe(2);
      const deleteButtons = screen.getAllByText("Delete");
      expect(deleteButtons.length).toBe(2);
    });
  });
});
