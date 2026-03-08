import { useState, useCallback } from 'react'
import type { AnalystInfo } from '../types/api'

const TOKEN_KEY = 'rf_admin_token'
const ANALYST_KEY = 'rf_analyst_info'
const API_BASE = (import.meta.env.VITE_API_URL ?? '') + '/api/v1'

function loadAnalystInfo(): AnalystInfo | null {
  try {
    const raw = localStorage.getItem(ANALYST_KEY)
    return raw ? (JSON.parse(raw) as AnalystInfo) : null
  } catch {
    return null
  }
}

export function useAuth() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY))
  const [analyst, setAnalyst] = useState<AnalystInfo | null>(() => loadAnalystInfo())

  const isAdmin = token !== null

  const login = useCallback(async (username: string, password: string): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/admin/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      if (!res.ok) return false
      const data = await res.json()
      if (data.token) {
        localStorage.setItem(TOKEN_KEY, data.token)
        setToken(data.token)
        if (data.analyst) {
          localStorage.setItem(ANALYST_KEY, JSON.stringify(data.analyst))
          setAnalyst(data.analyst)
        }
        return true
      }
      return false
    } catch {
      return false
    }
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(ANALYST_KEY)
    setToken(null)
    setAnalyst(null)
  }, [])

  const getAuthHeaders = useCallback((): Record<string, string> => {
    return token ? { Authorization: `Bearer ${token}` } : {}
  }, [token])

  return { token, isAdmin, analyst, login, logout, getAuthHeaders }
}
