/**
 * usePresence — SSE consumer hook for analyst presence updates.
 *
 * Connects to the /sse/presence endpoint and maintains a reactive list
 * of analyst presence entries. Automatically reconnects on disconnection.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getStorage } from "./useAuth";

const API_BASE = (import.meta.env.VITE_API_URL ?? "") + "/api/v1";

export interface PresenceEntry {
  analyst_id: number;
  analyst_name: string;
  is_online: boolean;
  current_alert_id: number | null;
  last_seen: number;
}

export interface UsePresenceResult {
  /** Current list of analyst presence entries. */
  analysts: PresenceEntry[];
  /** Whether the SSE connection is currently active. */
  connected: boolean;
  /** Get analysts currently viewing a specific alert. */
  getAlertViewers: (alertId: number) => PresenceEntry[];
}

export function usePresence(): UsePresenceResult {
  const [analysts, setAnalysts] = useState<PresenceEntry[]>([]);
  const [connected, setConnected] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(
    null
  );

  const connect = useCallback(() => {
    // Clean up any existing connection
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const token = getStorage().getItem("rf_admin_token");
    if (!token) {
      // No auth token, don't connect
      setConnected(false);
      return;
    }

    // EventSource doesn't support custom headers natively,
    // so we pass the token as a query parameter.
    const url = `${API_BASE}/sse/presence?token=${encodeURIComponent(token)}`;
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.addEventListener("presence", (event: MessageEvent) => {
      try {
        const data: PresenceEntry[] = JSON.parse(event.data);
        setAnalysts(data);
      } catch {
        // Ignore malformed messages
      }
    });

    es.addEventListener("open", () => {
      setConnected(true);
    });

    es.addEventListener("error", () => {
      setConnected(false);
      es.close();
      // Reconnect after 5 seconds
      reconnectTimeoutRef.current = setTimeout(connect, 5000);
    });
  }, []);

  useEffect(() => {
    connect();

    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
    };
  }, [connect]);

  const getAlertViewers = useCallback(
    (alertId: number): PresenceEntry[] => {
      return analysts.filter(
        (a) => a.is_online && a.current_alert_id === alertId
      );
    },
    [analysts]
  );

  return { analysts, connected, getAlertViewers };
}
