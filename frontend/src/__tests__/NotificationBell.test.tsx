import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { NotificationBell } from "../components/NotificationBell";
import type { NotificationEvent } from "../types/api";

const sampleNotifications: NotificationEvent[] = [
  {
    event_id: 1,
    event_type: "assignment",
    payload: { alert_id: 42 },
    is_read: false,
    created_at: new Date().toISOString(),
  },
  {
    event_id: 2,
    event_type: "handoff",
    payload: { alert_id: 7, from_analyst: "alice" },
    is_read: true,
    created_at: new Date().toISOString(),
  },
];

describe("NotificationBell", () => {
  it("renders bell icon", () => {
    render(
      <NotificationBell
        notifications={[]}
        unreadCount={0}
        onMarkRead={vi.fn()}
        onMarkAllRead={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("Notifications")).toBeInTheDocument();
  });

  it("shows unread badge when count > 0", () => {
    render(
      <NotificationBell
        notifications={sampleNotifications}
        unreadCount={3}
        onMarkRead={vi.fn()}
        onMarkAllRead={vi.fn()}
      />,
    );
    expect(screen.getByTestId("unread-badge")).toHaveTextContent("3");
  });

  it("hides badge when count is 0", () => {
    render(
      <NotificationBell
        notifications={[]}
        unreadCount={0}
        onMarkRead={vi.fn()}
        onMarkAllRead={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("unread-badge")).not.toBeInTheDocument();
  });

  it("dropdown opens on click", () => {
    render(
      <NotificationBell
        notifications={sampleNotifications}
        unreadCount={1}
        onMarkRead={vi.fn()}
        onMarkAllRead={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("notification-dropdown")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Notifications"));
    expect(screen.getByTestId("notification-dropdown")).toBeInTheDocument();
    expect(screen.getByText("New Assignment")).toBeInTheDocument();
    expect(screen.getByText("Alert Handoff")).toBeInTheDocument();
  });

  it("calls onMarkRead when clicking unread notification", () => {
    const onMarkRead = vi.fn();
    render(
      <NotificationBell
        notifications={sampleNotifications}
        unreadCount={1}
        onMarkRead={onMarkRead}
        onMarkAllRead={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByLabelText("Notifications"));
    fireEvent.click(screen.getByText("New Assignment"));
    expect(onMarkRead).toHaveBeenCalledWith(1);
  });

  it("calls onMarkAllRead when clicking mark all read", () => {
    const onMarkAllRead = vi.fn();
    render(
      <NotificationBell
        notifications={sampleNotifications}
        unreadCount={1}
        onMarkRead={vi.fn()}
        onMarkAllRead={onMarkAllRead}
      />,
    );
    fireEvent.click(screen.getByLabelText("Notifications"));
    fireEvent.click(screen.getByText("Mark all read"));
    expect(onMarkAllRead).toHaveBeenCalledTimes(1);
  });
});
