/**
 * AnalystPresenceIndicator — shows a colored dot indicating analyst status.
 *
 * - Green: online and viewing this alert
 * - Yellow: online but viewing a different alert
 * - Gray: offline
 *
 * Includes a tooltip with analyst name and current activity.
 */

import React from "react";

export interface AnalystPresenceProps {
  analystName: string;
  isOnline: boolean;
  currentAlertId: number | null;
  /** The alert currently being viewed by the user (context). */
  viewingAlertId?: number;
}

const dotStyle = (color: string): React.CSSProperties => ({
  display: "inline-block",
  width: 10,
  height: 10,
  borderRadius: "50%",
  backgroundColor: color,
  marginRight: 6,
  flexShrink: 0,
});

const containerStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  position: "relative",
  cursor: "default",
  padding: "2px 6px",
  fontSize: 13,
};

const tooltipStyle: React.CSSProperties = {
  position: "absolute",
  bottom: "calc(100% + 4px)",
  left: "50%",
  transform: "translateX(-50%)",
  backgroundColor: "#1e293b",
  color: "#f1f5f9",
  padding: "4px 8px",
  borderRadius: 4,
  fontSize: 12,
  whiteSpace: "nowrap",
  zIndex: 50,
  pointerEvents: "none",
};

function getDotColor(
  isOnline: boolean,
  currentAlertId: number | null,
  viewingAlertId?: number
): string {
  if (!isOnline) return "#9ca3af"; // gray-400
  if (viewingAlertId != null && currentAlertId === viewingAlertId) {
    return "#22c55e"; // green-500
  }
  return "#eab308"; // yellow-500
}

function getTooltipText(
  analystName: string,
  isOnline: boolean,
  currentAlertId: number | null
): string {
  if (!isOnline) return `${analystName} — offline`;
  if (currentAlertId != null) {
    return `${analystName} — viewing alert #${currentAlertId}`;
  }
  return `${analystName} — online`;
}

export default function AnalystPresenceIndicator({
  analystName,
  isOnline,
  currentAlertId,
  viewingAlertId,
}: AnalystPresenceProps) {
  const [showTooltip, setShowTooltip] = React.useState(false);

  const color = getDotColor(isOnline, currentAlertId, viewingAlertId);
  const tooltip = getTooltipText(analystName, isOnline, currentAlertId);

  return (
    <span
      style={containerStyle}
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
      data-testid="presence-indicator"
    >
      <span style={dotStyle(color)} data-testid="presence-dot" />
      <span>{analystName}</span>
      {showTooltip && <span style={tooltipStyle}>{tooltip}</span>}
    </span>
  );
}
