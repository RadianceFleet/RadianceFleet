/**
 * Unified SSE consumer — multiplexes alert, presence, and notification streams.
 * Uses fetch-event-source for Bearer auth support.
 */
import { useEffect, useRef } from "react";
import { fetchEventSource } from "@microsoft/fetch-event-source";
import { getStorage } from "./useAuth";

interface UseEventStreamOptions {
  enabled: boolean;
  minScore?: number;
  onAlert?: (alert: unknown) => void;
  onPresence?: (presence: unknown[]) => void;
  onNotification?: (notification: unknown) => void;
}

export function useEventStream(options: UseEventStreamOptions) {
  const { enabled, minScore = 51, onAlert, onPresence, onNotification } =
    options;
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!enabled) return;

    const controller = new AbortController();
    controllerRef.current = controller;
    const token = getStorage().getItem("rf_admin_token");

    const baseUrl = (import.meta.env.VITE_API_URL ?? "") + "/api/v1";

    fetchEventSource(`${baseUrl}/sse/events?min_score=${minScore}`, {
      signal: controller.signal,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      onmessage(ev) {
        if (!ev.data) return;
        try {
          const data = JSON.parse(ev.data);
          switch (ev.event) {
            case "alert":
              onAlert?.(data);
              break;
            case "presence":
              onPresence?.(data);
              break;
            case "notification":
              onNotification?.(data);
              break;
          }
        } catch {
          /* ignore parse errors */
        }
      },
      onerror() {
        // Retry handled by fetch-event-source
      },
    });

    return () => controller.abort();
  }, [enabled, minScore]); // eslint-disable-line react-hooks/exhaustive-deps
}
