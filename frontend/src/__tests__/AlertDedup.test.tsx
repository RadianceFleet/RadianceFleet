import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "./testUtils";
import { AlertGroupDetail } from "../components/AlertGroupDetail";

// Mock apiFetch
vi.mock("../lib/api", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "../lib/api";
const mockApiFetch = apiFetch as ReturnType<typeof vi.fn>;

const MOCK_GROUP = {
  group_id: 1,
  vessel_id: 10,
  corridor_id: 5,
  group_key: "abc123",
  primary_alert_id: 100,
  alert_count: 3,
  first_seen_utc: "2026-03-01T00:00:00",
  last_seen_utc: "2026-03-05T00:00:00",
  max_risk_score: 85,
  status: "active",
  created_at: "2026-03-01T00:00:00",
  members: [
    {
      gap_event_id: 100,
      vessel_id: 10,
      gap_start_utc: "2026-03-01T00:00:00",
      gap_end_utc: "2026-03-01T12:00:00",
      duration_minutes: 720,
      risk_score: 85,
      status: "new",
    },
    {
      gap_event_id: 101,
      vessel_id: 10,
      gap_start_utc: "2026-03-03T00:00:00",
      gap_end_utc: "2026-03-03T06:00:00",
      duration_minutes: 360,
      risk_score: 60,
      status: "new",
    },
    {
      gap_event_id: 102,
      vessel_id: 10,
      gap_start_utc: "2026-03-05T00:00:00",
      gap_end_utc: "2026-03-05T08:00:00",
      duration_minutes: 480,
      risk_score: 45,
      status: "under_review",
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AlertGroupDetail", () => {
  it("renders group header with stats", async () => {
    mockApiFetch.mockResolvedValueOnce(MOCK_GROUP);
    renderWithProviders(<AlertGroupDetail groupId={1} />);
    await waitFor(() => {
      expect(screen.getByText(/Group #1/)).toBeTruthy();
    });
    expect(screen.getByText(/3 alerts/)).toBeTruthy();
  });

  it("renders member alerts in table", async () => {
    mockApiFetch.mockResolvedValueOnce(MOCK_GROUP);
    renderWithProviders(<AlertGroupDetail groupId={1} />);
    await waitFor(() => {
      expect(screen.getByText("#100")).toBeTruthy();
    });
    expect(screen.getByText("#101")).toBeTruthy();
    expect(screen.getByText("#102")).toBeTruthy();
  });

  it("marks primary alert", async () => {
    mockApiFetch.mockResolvedValueOnce(MOCK_GROUP);
    const { container } = renderWithProviders(<AlertGroupDetail groupId={1} />);
    await waitFor(() => {
      expect(screen.getByText("#100")).toBeTruthy();
    });
    // The primary alert (ID 100) should have "Primary" label in its row
    const cells = container.querySelectorAll("td");
    const cellTexts = Array.from(cells).map((c) => c.textContent);
    expect(cellTexts.some((t) => t === "Primary")).toBe(true);
  });

  it("shows action buttons when authenticated", async () => {
    mockApiFetch.mockResolvedValueOnce(MOCK_GROUP);
    renderWithProviders(<AlertGroupDetail groupId={1} />, {
      auth: { authenticated: true, role: "admin" },
    });
    await waitFor(() => {
      expect(screen.getByText("Dismiss Group")).toBeTruthy();
    });
    expect(screen.getByText("True Positive")).toBeTruthy();
    expect(screen.getByText("False Positive")).toBeTruthy();
  });

  it("shows error state on fetch failure", async () => {
    mockApiFetch.mockRejectedValueOnce(new Error("Network error"));
    renderWithProviders(<AlertGroupDetail groupId={999} />);
    await waitFor(() => {
      expect(screen.getByText(/Error loading alert group/)).toBeTruthy();
    });
  });

  it("shows collapse/expand toggle", async () => {
    mockApiFetch.mockResolvedValueOnce(MOCK_GROUP);
    renderWithProviders(<AlertGroupDetail groupId={1} />);
    await waitFor(() => {
      expect(screen.getByText("Collapse")).toBeTruthy();
    });
  });
});
