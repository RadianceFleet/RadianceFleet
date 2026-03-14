import type { CSSProperties } from "react";

/** Section heading used above tables and detail cards. */
export const sectionHead: CSSProperties = {
  margin: "0 0 12px",
  fontSize: 14,
  color: "var(--text-muted)",
  textTransform: "uppercase",
  letterSpacing: 1,
};

/**
 * Label cell for key-value detail tables.
 * Pass `width` override when needed (default 200).
 */
export const labelCell: CSSProperties = {
  color: "var(--text-dim)",
  width: 200,
  fontSize: 13,
  paddingRight: 12,
  paddingBottom: 8,
  verticalAlign: "top",
};

/** Value cell paired with labelCell in detail tables. */
export const valueCell: CSSProperties = {
  fontSize: 13,
  paddingBottom: 8,
};

/** Standard table header cell. */
export const thStyle: CSSProperties = {
  padding: "8px 12px",
  textAlign: "left",
  fontWeight: 600,
  color: "var(--text-muted)",
  whiteSpace: "nowrap",
  fontSize: 12,
};

/** Sortable table header cell (adds cursor + userSelect). */
export const thSortable: CSSProperties = {
  ...thStyle,
  cursor: "pointer",
  userSelect: "none",
};

/** Standard table data cell. */
export const tdStyle: CSSProperties = {
  padding: "8px 12px",
  fontSize: 13,
};

/** Full-width collapsed-border table wrapper. */
export const tableStyle: CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
};

/** Background for thead rows. */
export const theadRow: CSSProperties = {
  background: "var(--bg-base)",
};

/** Bottom border for tbody rows. */
export const tbodyRow: CSSProperties = {
  borderBottom: "1px solid var(--border)",
};

/** Card-style section container. */
export const card: CSSProperties = {
  background: "var(--bg-card)",
  borderRadius: "var(--radius-md)",
  padding: 16,
  marginBottom: 16,
};

/** Standard action button base style. */
export const btnStyle: CSSProperties = {
  padding: "6px 14px",
  background: "var(--bg-card)",
  color: "var(--text-muted)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius)",
  cursor: "pointer",
  fontSize: 13,
};

/** Standard form input style. */
export const inputStyle: CSSProperties = {
  background: "var(--bg-base)",
  color: "var(--text-bright)",
  border: "1px solid var(--border)",
  padding: "6px 10px",
  borderRadius: "var(--radius)",
  fontSize: 13,
};

/** Flag risk category color map. */
export const flagRiskColors: Record<string, string> = {
  high: "var(--score-critical)",
  medium: "var(--score-medium)",
  low: "var(--score-low)",
  unknown: "var(--text-dim)",
};
