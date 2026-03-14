import { useState, useRef, useEffect } from "react";
import type { NotificationEvent } from "../types/api";

interface NotificationBellProps {
  notifications: NotificationEvent[];
  unreadCount: number;
  onMarkRead: (eventId: number) => void;
  onMarkAllRead: () => void;
}

function formatEventType(eventType: string): string {
  switch (eventType) {
    case "assignment":
      return "New Assignment";
    case "handoff":
      return "Alert Handoff";
    case "viewer_join":
      return "Viewer Joined";
    case "viewer_leave":
      return "Viewer Left";
    case "case_update":
      return "Case Update";
    default:
      return eventType;
  }
}

function formatTimestamp(ts: string | null): string {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    return d.toLocaleDateString();
  } catch {
    return "";
  }
}

export function NotificationBell({
  notifications,
  unreadCount,
  onMarkRead,
  onMarkAllRead,
}: NotificationBellProps) {
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
      return () =>
        document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [open]);

  return (
    <div ref={dropdownRef} style={{ position: "relative", display: "inline-block" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label="Notifications"
        style={{
          background: "none",
          border: "none",
          cursor: "pointer",
          fontSize: "1.4rem",
          position: "relative",
          padding: "4px 8px",
        }}
      >
        <span role="img" aria-label="bell">
          bell-icon
        </span>
        {unreadCount > 0 && (
          <span
            data-testid="unread-badge"
            style={{
              position: "absolute",
              top: 0,
              right: 0,
              background: "#e53e3e",
              color: "#fff",
              borderRadius: "50%",
              width: 18,
              height: 18,
              fontSize: "0.7rem",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: "bold",
            }}
          >
            {unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div
          data-testid="notification-dropdown"
          style={{
            position: "absolute",
            right: 0,
            top: "100%",
            width: 320,
            maxHeight: 400,
            overflowY: "auto",
            background: "#fff",
            border: "1px solid #e2e8f0",
            borderRadius: 8,
            boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
            zIndex: 1000,
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "8px 12px",
              borderBottom: "1px solid #e2e8f0",
            }}
          >
            <strong>Notifications</strong>
            {unreadCount > 0 && (
              <button
                onClick={() => onMarkAllRead()}
                style={{
                  background: "none",
                  border: "none",
                  color: "#3182ce",
                  cursor: "pointer",
                  fontSize: "0.8rem",
                }}
              >
                Mark all read
              </button>
            )}
          </div>
          {notifications.length === 0 ? (
            <div style={{ padding: "16px", textAlign: "center", color: "#a0aec0" }}>
              No notifications
            </div>
          ) : (
            notifications.map((notif) => (
              <div
                key={notif.event_id}
                onClick={() => {
                  if (!notif.is_read) onMarkRead(notif.event_id);
                }}
                style={{
                  padding: "10px 12px",
                  borderBottom: "1px solid #f7fafc",
                  background: notif.is_read ? "#fff" : "#ebf8ff",
                  cursor: notif.is_read ? "default" : "pointer",
                }}
              >
                <div style={{ fontWeight: notif.is_read ? "normal" : "bold", fontSize: "0.85rem" }}>
                  {formatEventType(notif.event_type)}
                </div>
                <div style={{ fontSize: "0.75rem", color: "#718096" }}>
                  {formatTimestamp(notif.created_at)}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
