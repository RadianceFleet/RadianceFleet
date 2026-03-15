import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor, fireEvent } from "@testing-library/react";
import { renderWithProviders } from "./testUtils";
import SatelliteBulkOrderPage from "../components/SatelliteBulkOrderPage";
import BulkOrderStatusTable from "../components/BulkOrderStatusTable";
import SatelliteBudgetDashboard from "../components/SatelliteBudgetDashboard";

const mockFetch = vi.fn();
globalThis.fetch = mockFetch;

beforeEach(() => {
  mockFetch.mockReset();
});

describe("SatelliteBulkOrderPage", () => {
  it("renders the create form", () => {
    renderWithProviders(<SatelliteBulkOrderPage />, { auth: { role: "admin" } });
    expect(screen.getByText("Create Bulk Satellite Order")).toBeInTheDocument();
    expect(screen.getByLabelText("Order Name")).toBeInTheDocument();
    expect(screen.getByLabelText("Priority (1-10)")).toBeInTheDocument();
    expect(screen.getByLabelText("Budget Cap (USD)")).toBeInTheDocument();
  });

  it("has add item button", () => {
    renderWithProviders(<SatelliteBulkOrderPage />, { auth: { role: "admin" } });
    expect(screen.getByText("+ Add Item")).toBeInTheDocument();
  });

  it("adds a new item row on click", () => {
    renderWithProviders(<SatelliteBulkOrderPage />, { auth: { role: "admin" } });
    const addBtn = screen.getByText("+ Add Item");
    fireEvent.click(addBtn);
    const vesselInputs = screen.getAllByPlaceholderText("Vessel ID");
    expect(vesselInputs.length).toBe(2);
  });

  it("removes an item row", () => {
    renderWithProviders(<SatelliteBulkOrderPage />, { auth: { role: "admin" } });
    fireEvent.click(screen.getByText("+ Add Item"));
    const removeBtns = screen.getAllByText("Remove");
    fireEvent.click(removeBtns[0]);
    expect(screen.getAllByPlaceholderText("Vessel ID").length).toBe(1);
  });

  it("disables submit when name is empty", () => {
    renderWithProviders(<SatelliteBulkOrderPage />, { auth: { role: "admin" } });
    const submitBtn = screen.getByText("Create Bulk Order");
    expect(submitBtn).toBeDisabled();
  });

  it("enables submit when name is provided", () => {
    renderWithProviders(<SatelliteBulkOrderPage />, { auth: { role: "admin" } });
    fireEvent.change(screen.getByLabelText("Order Name"), {
      target: { value: "Test Order" },
    });
    const submitBtn = screen.getByText("Create Bulk Order");
    expect(submitBtn).not.toBeDisabled();
  });
});

describe("BulkOrderStatusTable", () => {
  it("renders loading state", () => {
    mockFetch.mockReturnValue(new Promise(() => {}));
    renderWithProviders(<BulkOrderStatusTable />, { auth: { role: "admin" } });
    expect(screen.getByText("Loading bulk orders...")).toBeInTheDocument();
  });

  it("renders empty state", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ total: 0, orders: [] }),
    });
    renderWithProviders(<BulkOrderStatusTable />, { auth: { role: "admin" } });
    await waitFor(() => {
      expect(screen.getByText("No bulk orders found.")).toBeInTheDocument();
    });
  });

  it("renders orders table", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          total: 1,
          orders: [
            {
              bulk_order_id: 1,
              name: "Baltic Sweep",
              status: "draft",
              priority: 8,
              total_orders: 5,
              submitted_orders: 0,
              delivered_orders: 0,
              failed_orders: 0,
              estimated_total_cost_usd: 500,
              actual_total_cost_usd: null,
              budget_cap_usd: 1000,
              created_at: "2026-03-15T00:00:00",
            },
          ],
        }),
    });
    renderWithProviders(<BulkOrderStatusTable />, { auth: { role: "admin" } });
    await waitFor(() => {
      expect(screen.getByText("Baltic Sweep")).toBeInTheDocument();
    });
  });

  it("shows queue button for draft orders", async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          total: 1,
          orders: [
            {
              bulk_order_id: 1,
              name: "Test",
              status: "draft",
              priority: 5,
              total_orders: 3,
              submitted_orders: 0,
              delivered_orders: 0,
              failed_orders: 0,
              estimated_total_cost_usd: 300,
              actual_total_cost_usd: null,
              budget_cap_usd: null,
              created_at: "2026-03-15T00:00:00",
            },
          ],
        }),
    });
    renderWithProviders(<BulkOrderStatusTable />, { auth: { role: "admin" } });
    await waitFor(() => {
      expect(screen.getByText("Queue")).toBeInTheDocument();
    });
  });

  it("has status filter dropdown", () => {
    mockFetch.mockReturnValue(new Promise(() => {}));
    renderWithProviders(<BulkOrderStatusTable />, { auth: { role: "admin" } });
    expect(screen.getByLabelText(/Filter by status/)).toBeInTheDocument();
  });
});

describe("SatelliteBudgetDashboard", () => {
  it("renders loading state", () => {
    mockFetch.mockReturnValue(new Promise(() => {}));
    renderWithProviders(<SatelliteBudgetDashboard />, { auth: { role: "admin" } });
    expect(screen.getByText("Loading budget data...")).toBeInTheDocument();
  });

  it("renders error state", async () => {
    mockFetch.mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ detail: "Error" }),
      statusText: "Error",
    });
    renderWithProviders(<SatelliteBudgetDashboard />, { auth: { role: "admin" } });
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
  });
});
