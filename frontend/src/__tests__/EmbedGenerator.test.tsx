import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { renderWithProviders } from "./testUtils";
import EmbedGeneratorPage from "../pages/EmbedGeneratorPage";

// Mock fetch for vessels list
const mockFetch = vi.fn();
globalThis.fetch = mockFetch;

beforeEach(() => {
  mockFetch.mockReset();
  mockFetch.mockResolvedValue({
    ok: true,
    json: () =>
      Promise.resolve({
        items: [
          { vessel_id: 1, name: "SHADOW TANKER", mmsi: "123456789" },
          { vessel_id: 2, name: "GHOST CARRIER", mmsi: "987654321" },
        ],
        total: 2,
      }),
  });
});

describe("EmbedGeneratorPage", () => {
  it("renders the form with all controls", async () => {
    renderWithProviders(<EmbedGeneratorPage />, {
      auth: { role: "admin", authenticated: true },
    });
    expect(screen.getByText("Embed Widget Generator")).toBeTruthy();
    expect(screen.getByTestId("vessel-select")).toBeTruthy();
    expect(screen.getByTestId("type-select")).toBeTruthy();
    expect(screen.getByTestId("apikey-input")).toBeTruthy();
    expect(screen.getByTestId("copy-button")).toBeTruthy();
  });

  it("generates iframe code with correct params", async () => {
    renderWithProviders(<EmbedGeneratorPage />, {
      auth: { role: "admin", authenticated: true },
    });

    // Wait for vessels to load
    await waitFor(() => {
      const select = screen.getByTestId("vessel-select") as HTMLSelectElement;
      expect(select.options.length).toBeGreaterThan(1);
    });

    // Select a vessel
    fireEvent.change(screen.getByTestId("vessel-select"), {
      target: { value: "1" },
    });

    // Enter API key
    fireEvent.change(screen.getByTestId("apikey-input"), {
      target: { value: "test-key-123" },
    });

    const codeArea = screen.getByTestId("embed-code") as HTMLTextAreaElement;
    expect(codeArea.value).toContain("iframe");
    expect(codeArea.value).toContain("vessel=1");
    expect(codeArea.value).toContain("apiKey=test-key-123");
    expect(codeArea.value).toContain("type=summary");
  });

  it("copies embed code to clipboard", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    renderWithProviders(<EmbedGeneratorPage />, {
      auth: { role: "admin", authenticated: true },
    });

    fireEvent.click(screen.getByTestId("copy-button"));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalled();
    });
  });

  it("shows preview iframe when vessel and apiKey are set", async () => {
    renderWithProviders(<EmbedGeneratorPage />, {
      auth: { role: "admin", authenticated: true },
    });

    await waitFor(() => {
      const select = screen.getByTestId("vessel-select") as HTMLSelectElement;
      expect(select.options.length).toBeGreaterThan(1);
    });

    fireEvent.change(screen.getByTestId("vessel-select"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByTestId("apikey-input"), {
      target: { value: "key123" },
    });

    expect(screen.getByTestId("preview-iframe")).toBeTruthy();
  });

  it("does not show preview without vessel selected", () => {
    renderWithProviders(<EmbedGeneratorPage />, {
      auth: { role: "admin", authenticated: true },
    });

    expect(screen.queryByTestId("preview-iframe")).toBeNull();
    expect(screen.getByText(/Select a vessel/)).toBeTruthy();
  });
});
