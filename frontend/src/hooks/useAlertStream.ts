import { useEffect, useRef, useCallback, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { fetchEventSource } from '@microsoft/fetch-event-source'

const API_BASE = (import.meta.env.VITE_API_URL ?? '') + '/api/v1'

export interface StreamAlert {
  gap_event_id: number
  vessel_id: number
  risk_score: number
  gap_start_utc: string | null
  duration_minutes: number
  status: string
}

interface UseAlertStreamOptions {
  minScore?: number
  enabled?: boolean
  onAlert?: (alert: StreamAlert) => void
}

export function useAlertStream(options: UseAlertStreamOptions = {}) {
  const { minScore = 51, enabled = true, onAlert } = options
  const queryClient = useQueryClient()
  const controllerRef = useRef<AbortController | null>(null)
  const [connected, setConnected] = useState(false)
  const [lastAlert, setLastAlert] = useState<StreamAlert | null>(null)

  const connect = useCallback(() => {
    if (!enabled) return

    const token = localStorage.getItem('rf_admin_token')
    const ctrl = new AbortController()
    controllerRef.current = ctrl

    fetchEventSource(`${API_BASE}/sse/alerts?min_score=${minScore}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      signal: ctrl.signal,
      openWhenHidden: true,

      onopen: async (response) => {
        if (response.ok) {
          setConnected(true)
        } else {
          throw new Error(`SSE open failed: ${response.status}`)
        }
      },

      onmessage: (event) => {
        if (event.event === 'ping' || !event.data) return
        if (event.event === 'alert') {
          try {
            const alert: StreamAlert = JSON.parse(event.data)
            setLastAlert(alert)
            onAlert?.(alert)
            // Invalidate alert queries to refresh lists
            queryClient.invalidateQueries({ queryKey: ['alerts'] })
            queryClient.invalidateQueries({ queryKey: ['alerts-map'] })
            queryClient.invalidateQueries({ queryKey: ['stats'] })
          } catch {
            // ignore parse errors
          }
        }
      },

      onclose: () => {
        setConnected(false)
      },

      onerror: (err) => {
        setConnected(false)
        // Exponential backoff handled by throwing to trigger retry
        console.warn('SSE error, will reconnect:', err)
      },
    })
  }, [enabled, minScore, onAlert, queryClient])

  useEffect(() => {
    connect()
    return () => {
      controllerRef.current?.abort()
      setConnected(false)
    }
  }, [connect])

  return { connected, lastAlert }
}
