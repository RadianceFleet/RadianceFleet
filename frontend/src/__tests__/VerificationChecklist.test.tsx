import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "./testUtils";
import type { Checklist } from "../hooks/useVerificationChecklist";

// Default: checklist exists with items
const mockChecklist: Checklist = {
  checklist_id: 1,
  alert_id: 1,
  checklist_template: "standard",
  created_by: 1,
  created_at: "2026-03-15T10:00:00",
  completed_at: null,
  completed_by: null,
  items: [
    {
      item_id: 1,
      item_key: "check_ais_gap_duration",
      label: "Verify AIS gap duration is above threshold",
      is_checked: false,
      checked_by: null,
      checked_at: null,
      notes: null,
      sort_order: 0,
    },
    {
      item_id: 2,
      item_key: "check_vessel_history",
      label: "Review vessel history for prior incidents",
      is_checked: true,
      checked_by: 1,
      checked_at: "2026-03-15T11:00:00",
      notes: "Checked the history",
      sort_order: 1,
    },
  ],
};

const mockMutate = vi.fn();
let mockChecklistData: Checklist | undefined = mockChecklist;
let mockChecklistError: Error | null = null;
let mockChecklistLoading = false;

vi.mock("../hooks/useVerificationChecklist", () => ({
  useChecklist: () => ({
    data: mockChecklistData,
    isLoading: mockChecklistLoading,
    error: mockChecklistError,
  }),
  useCreateChecklist: () => ({
    mutate: mockMutate,
    isPending: false,
    isError: false,
  }),
  useToggleChecklistItem: () => ({
    mutate: mockMutate,
    isPending: false,
  }),
}));

import { VerificationChecklist } from "../components/VerificationChecklist";

describe("VerificationChecklist", () => {
  beforeEach(() => {
    mockChecklistData = mockChecklist;
    mockChecklistError = null;
    mockChecklistLoading = false;
    mockMutate.mockClear();
  });

  it("renders checklist items", () => {
    renderWithProviders(<VerificationChecklist alertId="1" />);
    expect(
      screen.getByText("Verify AIS gap duration is above threshold")
    ).toBeInTheDocument();
    expect(
      screen.getByText("Review vessel history for prior incidents")
    ).toBeInTheDocument();
  });

  it("shows progress bar with correct count", () => {
    renderWithProviders(<VerificationChecklist alertId="1" />);
    expect(screen.getByText("1 / 2 completed")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
  });

  it("displays template name", () => {
    renderWithProviders(<VerificationChecklist alertId="1" />);
    expect(screen.getByText("Standard Review")).toBeInTheDocument();
  });

  it("shows checked item metadata", () => {
    renderWithProviders(<VerificationChecklist alertId="1" />);
    expect(
      screen.getByText(/Checked by analyst #1/)
    ).toBeInTheDocument();
  });

  it("shows item notes", () => {
    renderWithProviders(<VerificationChecklist alertId="1" />);
    expect(screen.getByText("Checked the history")).toBeInTheDocument();
  });

  it("shows loading state", () => {
    mockChecklistLoading = true;
    mockChecklistData = undefined;
    renderWithProviders(<VerificationChecklist alertId="1" />);
    expect(screen.getByText("Loading checklist...")).toBeInTheDocument();
  });

  it("shows create button when no checklist exists", () => {
    mockChecklistData = undefined;
    mockChecklistError = new Error("Not found");
    renderWithProviders(<VerificationChecklist alertId="1" />);
    expect(screen.getByText("Create Checklist")).toBeInTheDocument();
  });

  it("hides create button in readOnly mode when no checklist", () => {
    mockChecklistData = undefined;
    mockChecklistError = new Error("Not found");
    renderWithProviders(
      <VerificationChecklist alertId="1" readOnly />
    );
    expect(screen.queryByText("Create Checklist")).not.toBeInTheDocument();
    expect(screen.getByText("No checklist available.")).toBeInTheDocument();
  });

  it("renders checkboxes", () => {
    renderWithProviders(<VerificationChecklist alertId="1" />);
    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes).toHaveLength(2);
    expect(checkboxes[0]).not.toBeChecked();
    expect(checkboxes[1]).toBeChecked();
  });

  it("shows completion banner when completed", () => {
    mockChecklistData = {
      ...mockChecklist,
      completed_at: "2026-03-15T12:00:00",
      completed_by: 1,
    };
    renderWithProviders(<VerificationChecklist alertId="1" />);
    expect(
      screen.getByText(/Completed at 2026-03-15/)
    ).toBeInTheDocument();
  });

  it("disables checkboxes in readOnly mode", () => {
    renderWithProviders(
      <VerificationChecklist alertId="1" readOnly />
    );
    const checkboxes = screen.getAllByRole("checkbox");
    checkboxes.forEach((cb) => {
      expect(cb).toBeDisabled();
    });
  });

  it("shows notes input for unchecked items when not readOnly", () => {
    renderWithProviders(<VerificationChecklist alertId="1" />);
    const notesInputs = screen.getAllByPlaceholderText("Add notes (optional)");
    // Only unchecked items get notes input
    expect(notesInputs).toHaveLength(1);
  });

  it("calls mutate on checkbox change", async () => {
    const user = userEvent.setup();
    renderWithProviders(<VerificationChecklist alertId="1" />);
    const checkboxes = screen.getAllByRole("checkbox");
    await user.click(checkboxes[0]);
    expect(mockMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        itemId: 1,
        isChecked: true,
      })
    );
  });

  it("renders section heading", () => {
    renderWithProviders(<VerificationChecklist alertId="1" />);
    expect(
      screen.getByText("Evidence Verification Checklist")
    ).toBeInTheDocument();
  });
});
