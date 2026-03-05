import { useState, useCallback } from 'react'

const TOKEN_KEY = 'rf_admin_token'
const API_BASE = (import.meta.env.VITE_API_URL ?? '') + '/api/v1'

export function useAuth() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem(TOKEN_KEY))

  const isAdmin = token !== null

  const login = useCallback(async (password: string): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/admin/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      })
      if (!res.ok) return false
      const data = await res.json()
      if (data.token) {
        localStorage.setItem(TOKEN_KEY, data.token)
        setToken(data.token)
        return true
      }
      return false
    } catch {
      return false
    }
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY)
    setToken(null)
  }, [])

  const getAuthHeaders = useCallback((): Record<string, string> => {
    return token ? { Authorization: `Bearer ${token}` } : {}
  }, [token])

  return { token, isAdmin, login, logout, getAuthHeaders }
}
