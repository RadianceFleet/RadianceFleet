import { useState, useCallback } from "react";
import { apiFetch } from "../lib/api";
import type { NotificationEvent } from "../types/api";

export function useNotifications() {
  const [notifications, setNotifications] = useState<NotificationEvent[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);

  const addNotification = useCallback((notif: NotificationEvent) => {
    setNotifications((prev) => [notif, ...prev].slice(0, 50));
    if (!notif.is_read) setUnreadCount((prev) => prev + 1);
  }, []);

  const markRead = useCallback(async (eventId: number) => {
    await apiFetch(`/notifications/${eventId}/read`, { method: "POST" });
    setNotifications((prev) =>
      prev.map((n) =>
        n.event_id === eventId ? { ...n, is_read: true } : n,
      ),
    );
    setUnreadCount((prev) => Math.max(0, prev - 1));
  }, []);

  const markAllRead = useCallback(async () => {
    await apiFetch("/notifications/read-all", { method: "POST" });
    setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
    setUnreadCount(0);
  }, []);

  return { notifications, unreadCount, addNotification, markRead, markAllRead };
}
