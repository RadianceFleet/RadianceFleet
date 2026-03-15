import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "./testUtils";
import { NotificationRulesPage } from "../components/NotificationRulesPage";

// Mock apiFetch
vi.mock("../lib/api", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "../lib/api";
const mockApiFetch = vi.mocked(apiFetch);

const mockRules = {
  rules: [
    {
      rule_id: 1,
      name: "High Risk Slack",
      is_active: true,
      created_by: 1,
      created_at: "2026-03-15T10:00:00",
      updated_at: null,
      min_score: 70,
      max_score: null,
      corridor_ids_json: [1, 2],
      vessel_flags_json: ["RU", "CM"],
      alert_statuses_json: null,
      vessel_types_json: null,
      scoring_signals_json: null,
      time_window_start: null,
      time_window_end: null,
      channel: "slack",
      destination: "#alerts",
      message_template: null,
      throttle_minutes: 30,
    },
  ],
  total: 1,
};

describe("NotificationRulesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApiFetch.mockResolvedValue(mockRules);
  });

  it("renders rule list", async () => {
    renderWithProviders(<NotificationRulesPage />, { auth: { role: "admin" } });
    await waitFor(() => {
      expect(screen.getByText("High Risk Slack")).toBeTruthy();
    });
  });

  it("shows channel and destination", async () => {
    renderWithProviders(<NotificationRulesPage />, { auth: { role: "admin" } });
    await waitFor(() => {
      expect(screen.getByText("#alerts")).toBeTruthy();
    });
    // "slack" appears both in the table cell and the form select option
    const cells = screen.getAllByText("slack");
    expect(cells.length).toBeGreaterThanOrEqual(1);
  });

  it("shows active status", async () => {
    renderWithProviders(<NotificationRulesPage />, { auth: { role: "admin" } });
    await waitFor(() => {
      expect(screen.getByText("Yes")).toBeTruthy();
    });
  });

  it("has create form with required fields", async () => {
    renderWithProviders(<NotificationRulesPage />, { auth: { role: "admin" } });
    await waitFor(() => {
      expect(screen.getByText("Create Rule")).toBeTruthy();
      expect(screen.getByText("Create")).toBeTruthy();
    });
  });

  it("shows edit form when Edit clicked", async () => {
    renderWithProviders(<NotificationRulesPage />, { auth: { role: "admin" } });
    await waitFor(() => screen.getByText("High Risk Slack"));
    const editBtn = screen.getByText("Edit");
    await userEvent.click(editBtn);
    expect(screen.getByText("Edit Rule")).toBeTruthy();
    expect(screen.getByText("Cancel")).toBeTruthy();
  });

  it("shows total count", async () => {
    renderWithProviders(<NotificationRulesPage />, { auth: { role: "admin" } });
    await waitFor(() => {
      expect(screen.getByText("Rules (1)")).toBeTruthy();
    });
  });
});
