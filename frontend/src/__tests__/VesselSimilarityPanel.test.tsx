import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { renderWithProviders } from "./testUtils";

const mockSimilarVessels = [
  {
    source_vessel_id: 1,
    target_vessel_id: 10,
    fingerprint_distance: 2.5,
    fingerprint_similarity: 0.8,
    fingerprint_band: "near",
    ownership_similarity_score: 0.55,
    ownership_breakdown: {
      shared_cluster: true,
      shared_ism_manager: false,
      shared_pi_club: true,
      same_owner_name: false,
      same_country: true,
    },
    composite_similarity_score: 0.7,
    similarity_tier: "HIGH",
  },
  {
    source_vessel_id: 1,
    target_vessel_id: 20,
    fingerprint_distance: 5.0,
    fingerprint_similarity: 0.67,
    fingerprint_band: "moderate",
    ownership_similarity_score: 0.0,
    ownership_breakdown: {
      shared_cluster: false,
      shared_ism_manager: false,
      shared_pi_club: false,
      same_owner_name: false,
      same_country: false,
    },
    composite_similarity_score: 0.4,
    similarity_tier: "MEDIUM",
  },
];

vi.mock("../hooks/useVesselSimilarity", () => ({
  useVesselSimilarity: (vesselId: string | undefined) => {
    if (!vesselId) {
      return { data: undefined, isLoading: false, error: null };
    }
    return {
      data: {
        vessel_id: 1,
        similar_vessels: mockSimilarVessels,
        total: 2,
      },
      isLoading: false,
      error: null,
    };
  },
}));

import { VesselSimilarityPanel } from "../components/VesselSimilarityPanel";

describe("VesselSimilarityPanel", () => {
  it("renders collapsed by default", () => {
    renderWithProviders(<VesselSimilarityPanel vesselId="1" />);
    expect(screen.getByText(/Similar Vessels/)).toBeDefined();
    // Table should not be visible when collapsed
    expect(screen.queryByText("Vessel #10")).toBeNull();
  });

  it("expands when toggle is clicked", () => {
    renderWithProviders(<VesselSimilarityPanel vesselId="1" />);
    fireEvent.click(screen.getByTestId("similarity-toggle"));
    expect(screen.getByText("2 found")).toBeDefined();
    expect(screen.getByText("Vessel #10")).toBeDefined();
    expect(screen.getByText("Vessel #20")).toBeDefined();
  });

  it("shows tier badges for results", () => {
    renderWithProviders(<VesselSimilarityPanel vesselId="1" />);
    fireEvent.click(screen.getByTestId("similarity-toggle"));
    expect(screen.getByText("HIGH")).toBeDefined();
    expect(screen.getByText("MEDIUM")).toBeDefined();
  });

  it("shows ownership indicators for matching ownership", () => {
    renderWithProviders(<VesselSimilarityPanel vesselId="1" />);
    fireEvent.click(screen.getByTestId("similarity-toggle"));
    expect(screen.getByText("Cluster")).toBeDefined();
    expect(screen.getByText("P&I")).toBeDefined();
    expect(screen.getByText("Country")).toBeDefined();
  });

  it("expands detail row when Details button is clicked", () => {
    renderWithProviders(<VesselSimilarityPanel vesselId="1" />);
    fireEvent.click(screen.getByTestId("similarity-toggle"));
    fireEvent.click(screen.getByTestId("expand-details-10"));
    expect(screen.getByText("Fingerprint Distance")).toBeDefined();
    expect(screen.getByText(/2\.50/)).toBeDefined();
  });

  it("collapses detail row when Hide button is clicked", () => {
    renderWithProviders(<VesselSimilarityPanel vesselId="1" />);
    fireEvent.click(screen.getByTestId("similarity-toggle"));
    fireEvent.click(screen.getByTestId("expand-details-10"));
    expect(screen.getByText("Hide")).toBeDefined();
    fireEvent.click(screen.getByTestId("expand-details-10"));
    expect(screen.queryByText("Fingerprint Distance")).toBeNull();
  });

  it("shows composite score bar with percentage", () => {
    renderWithProviders(<VesselSimilarityPanel vesselId="1" />);
    fireEvent.click(screen.getByTestId("similarity-toggle"));
    expect(screen.getByText("70%")).toBeDefined();
    expect(screen.getByText("40%")).toBeDefined();
  });
});
