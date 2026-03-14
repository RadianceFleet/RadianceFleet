import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { renderWithProviders } from "./testUtils";
import { NetworkGraph } from "../components/NetworkGraph";
import { NetworkGraphFilters } from "../components/NetworkGraphFilters";
import { NetworkNodeTooltip } from "../components/NetworkNodeTooltip";
import type { NetworkNode, NetworkEdge } from "../hooks/useOwnershipNetwork";

const makeNode = (overrides: Partial<NetworkNode> = {}): NetworkNode => ({
  id: "owner-1",
  type: "company",
  label: "Test Corp",
  layer: 0,
  is_sanctioned: false,
  is_spv: false,
  jurisdiction: "US",
  ...overrides,
});

const sampleNodes: NetworkNode[] = [
  makeNode({ id: "owner-1", label: "Root Corp", layer: 0 }),
  makeNode({ id: "owner-2", label: "Middle Ltd", layer: 1 }),
  makeNode({ id: "owner-3", label: "Leaf LLC", layer: 2, is_spv: true }),
  makeNode({ id: "vessel-1", type: "vessel", label: "Test Ship", layer: 3, vessel_id: 1 }),
];

const sampleEdges: NetworkEdge[] = [
  { source: "owner-2", target: "owner-1", relationship: "subsidiary" },
  { source: "owner-3", target: "owner-2", relationship: "subsidiary" },
  { source: "owner-3", target: "vessel-1", relationship: "owns" },
];

describe("NetworkGraph", () => {
  it("renders SVG with nodes and edges", () => {
    renderWithProviders(
      <NetworkGraph nodes={sampleNodes} edges={sampleEdges} />,
    );

    expect(screen.getByTestId("network-graph-svg")).toBeTruthy();
    expect(screen.getAllByTestId("network-node")).toHaveLength(4);
    expect(screen.getAllByTestId("network-edge")).toHaveLength(3);
  });

  it("renders empty state when no nodes", () => {
    renderWithProviders(<NetworkGraph nodes={[]} edges={[]} />);

    expect(screen.getByTestId("network-empty")).toBeTruthy();
    expect(screen.getByText("No ownership data available.")).toBeTruthy();
  });

  it("renders company nodes as rectangles and vessel nodes as circles", () => {
    renderWithProviders(
      <NetworkGraph nodes={sampleNodes} edges={sampleEdges} />,
    );

    const nodes = screen.getAllByTestId("network-node");
    const companyNodes = nodes.filter(
      (n) => n.getAttribute("data-node-type") === "company",
    );
    const vesselNodes = nodes.filter(
      (n) => n.getAttribute("data-node-type") === "vessel",
    );
    expect(companyNodes).toHaveLength(3);
    expect(vesselNodes).toHaveLength(1);
  });

  it("highlights sanctioned nodes with red color", () => {
    const sanctionedNodes = [
      makeNode({
        id: "owner-10",
        label: "Bad Corp",
        layer: 0,
        is_sanctioned: true,
      }),
    ];
    renderWithProviders(
      <NetworkGraph nodes={sanctionedNodes} edges={[]} />,
    );

    const svg = screen.getByTestId("network-graph-svg");
    // The rect should have a red stroke (#ef4444)
    const rect = svg.querySelector("rect");
    expect(rect?.getAttribute("stroke")).toBe("#ef4444");
  });

  it("highlights SPV nodes with orange color", () => {
    const spvNodes = [
      makeNode({ id: "owner-20", label: "Shell Co", layer: 2, is_spv: true }),
    ];
    renderWithProviders(<NetworkGraph nodes={spvNodes} edges={[]} />);

    const svg = screen.getByTestId("network-graph-svg");
    const rect = svg.querySelector("rect");
    expect(rect?.getAttribute("stroke")).toBe("#f59e0b");
  });

  it("uses blue for default nodes", () => {
    const defaultNodes = [
      makeNode({ id: "owner-30", label: "Normal Corp", layer: 0 }),
    ];
    renderWithProviders(<NetworkGraph nodes={defaultNodes} edges={[]} />);

    const svg = screen.getByTestId("network-graph-svg");
    const rect = svg.querySelector("rect");
    expect(rect?.getAttribute("stroke")).toBe("#3b82f6");
  });
});

describe("NetworkGraphFilters", () => {
  it("renders all filter controls", () => {
    const onFiltersChange = vi.fn();
    renderWithProviders(
      <NetworkGraphFilters
        filters={{}}
        onFiltersChange={onFiltersChange}
        jurisdictions={["US", "PA", "MH"]}
      />,
    );

    expect(screen.getByTestId("filter-sanctioned")).toBeTruthy();
    expect(screen.getByTestId("filter-spv")).toBeTruthy();
    expect(screen.getByTestId("filter-jurisdiction")).toBeTruthy();
    expect(screen.getByTestId("filter-depth")).toBeTruthy();
  });

  it("calls onFiltersChange when sanctioned toggle clicked", () => {
    const onFiltersChange = vi.fn();
    renderWithProviders(
      <NetworkGraphFilters
        filters={{ sanctioned_only: false }}
        onFiltersChange={onFiltersChange}
      />,
    );

    fireEvent.click(screen.getByTestId("filter-sanctioned"));
    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ sanctioned_only: true }),
    );
  });
});

describe("NetworkNodeTooltip", () => {
  it("renders node details", () => {
    const node = makeNode({
      label: "Shell Corp",
      jurisdiction: "PA",
      is_sanctioned: true,
      is_spv: true,
    });
    renderWithProviders(
      <NetworkNodeTooltip node={node} x={100} y={100} />,
    );

    expect(screen.getByText("Shell Corp")).toBeTruthy();
    expect(screen.getByText("Jurisdiction: PA")).toBeTruthy();
    expect(screen.getByText("Sanctioned")).toBeTruthy();
    expect(screen.getByText("SPV Detected")).toBeTruthy();
  });
});
