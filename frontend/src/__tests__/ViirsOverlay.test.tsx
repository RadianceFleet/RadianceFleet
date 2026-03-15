import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "./testUtils";

// Mock react-leaflet components
vi.mock("react-leaflet", () => ({
  CircleMarker: ({ children, center, radius }: any) => (
    <div data-testid="circle-marker" data-lat={center[0]} data-lon={center[1]} data-radius={radius}>
      {children}
    </div>
  ),
  Marker: ({ children, position }: any) => (
    <div data-testid="marker" data-lat={position[0]} data-lon={position[1]}>
      {children}
    </div>
  ),
  Popup: ({ children }: any) => <div data-testid="popup">{children}</div>,
}));

// Mock leaflet
vi.mock("leaflet", () => ({
  default: {
    divIcon: () => ({}),
  },
}));

const mockViirsData = [
  {
    detection_id: 1,
    scene_id: "viirs-20240615",
    latitude: 35.5,
    longitude: 28.3,
    detection_timestamp_utc: "2024-06-15T12:00:00",
    estimated_length_m: 120,
    vessel_type_estimate: "tanker",
    confidence: 0.85,
    matched_vessel_id: 7,
  },
  {
    detection_id: 2,
    scene_id: "viirs-20240616",
    latitude: 36.0,
    longitude: 29.0,
    detection_timestamp_utc: "2024-06-16T08:30:00",
    estimated_length_m: null,
    vessel_type_estimate: null,
    confidence: 0.6,
    matched_vessel_id: null,
  },
];

const mockSarData = [
  {
    detection_id: 10,
    scene_id: "gfw-sar-sentinel1-20240601",
    latitude: -5.1,
    longitude: 42.7,
    detection_timestamp_utc: "2024-06-01T14:20:00",
    estimated_length_m: 95,
    vessel_type_estimate: "cargo",
    confidence: 0.78,
    matched_vessel_id: null,
  },
];

vi.mock("../hooks/useViirsDetections", () => ({
  useViirsDetections: () => ({ data: mockViirsData, isLoading: false }),
}));

vi.mock("../hooks/useSarDetections", () => ({
  useSarDetections: () => ({ data: mockSarData, isLoading: false }),
}));

describe("ViirsOverlay", () => {
  it("renders circle markers for VIIRS detections", async () => {
    const { ViirsOverlay } = await import("../components/map/ViirsOverlay");
    renderWithProviders(<ViirsOverlay />);

    const markers = screen.getAllByTestId("circle-marker");
    expect(markers).toHaveLength(2);
  });

  it("sets marker position from latitude/longitude", async () => {
    const { ViirsOverlay } = await import("../components/map/ViirsOverlay");
    renderWithProviders(<ViirsOverlay />);

    const markers = screen.getAllByTestId("circle-marker");
    expect(markers[0].getAttribute("data-lat")).toBe("35.5");
    expect(markers[0].getAttribute("data-lon")).toBe("28.3");
  });

  it("scales radius by confidence", async () => {
    const { ViirsOverlay } = await import("../components/map/ViirsOverlay");
    renderWithProviders(<ViirsOverlay />);

    const markers = screen.getAllByTestId("circle-marker");
    const r1 = Number(markers[0].getAttribute("data-radius"));
    const r2 = Number(markers[1].getAttribute("data-radius"));
    // Higher confidence = larger radius
    expect(r1).toBeGreaterThan(r2);
  });

  it("displays popup with VIIRS label", async () => {
    const { ViirsOverlay } = await import("../components/map/ViirsOverlay");
    renderWithProviders(<ViirsOverlay />);

    const popups = screen.getAllByTestId("popup");
    expect(popups[0].textContent).toContain("VIIRS Nightlight");
  });

  it("shows confidence percentage in popup", async () => {
    const { ViirsOverlay } = await import("../components/map/ViirsOverlay");
    renderWithProviders(<ViirsOverlay />);

    const popups = screen.getAllByTestId("popup");
    expect(popups[0].textContent).toContain("85%");
  });

  it("shows matched vessel ID when present", async () => {
    const { ViirsOverlay } = await import("../components/map/ViirsOverlay");
    renderWithProviders(<ViirsOverlay />);

    const popups = screen.getAllByTestId("popup");
    expect(popups[0].textContent).toContain("7");
    expect(popups[1].textContent).toContain("None");
  });
});

describe("SarOverlay", () => {
  it("renders markers for SAR detections", async () => {
    const { SarOverlay } = await import("../components/map/SarOverlay");
    renderWithProviders(<SarOverlay />);

    const markers = screen.getAllByTestId("marker");
    expect(markers).toHaveLength(1);
  });

  it("sets marker position correctly", async () => {
    const { SarOverlay } = await import("../components/map/SarOverlay");
    renderWithProviders(<SarOverlay />);

    const marker = screen.getByTestId("marker");
    expect(marker.getAttribute("data-lat")).toBe("-5.1");
    expect(marker.getAttribute("data-lon")).toBe("42.7");
  });

  it("displays popup with SAR label", async () => {
    const { SarOverlay } = await import("../components/map/SarOverlay");
    renderWithProviders(<SarOverlay />);

    const popup = screen.getByTestId("popup");
    expect(popup.textContent).toContain("SAR Detection");
  });

  it("shows estimated length in popup", async () => {
    const { SarOverlay } = await import("../components/map/SarOverlay");
    renderWithProviders(<SarOverlay />);

    const popup = screen.getByTestId("popup");
    expect(popup.textContent).toContain("95m");
  });

  it("shows vessel type in popup", async () => {
    const { SarOverlay } = await import("../components/map/SarOverlay");
    renderWithProviders(<SarOverlay />);

    const popup = screen.getByTestId("popup");
    expect(popup.textContent).toContain("cargo");
  });

  it("shows confidence in popup", async () => {
    const { SarOverlay } = await import("../components/map/SarOverlay");
    renderWithProviders(<SarOverlay />);

    const popup = screen.getByTestId("popup");
    expect(popup.textContent).toContain("78%");
  });
});
