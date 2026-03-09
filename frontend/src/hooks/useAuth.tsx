import { useState, useCallback, useEffect, useContext, createContext } from 'react'
import type { AnalystInfo } from '../types/api'

const TOKEN_KEY = 'rf_admin_token'
const ANALYST_KEY = 'rf_analyst_info'
const API_BASE = (import.meta.env.VITE_API_URL ?? '') + '/api/v1'

const memoryStore = new Map<string, string>()

export function getStorage(): Storage {
  try {
    const s = typeof window !== 'undefined' ? window.localStorage : globalThis.localStorage
    if (s && typeof s.getItem === 'function') return s
  } catch { /* unavailable */ }
  // Fallback for test environments where localStorage is unavailable
  return {
    getItem: (k: string) => memoryStore.get(k) ?? null,
    setItem: (k: string, v: string) => { memoryStore.set(k, v) },
    removeItem: (k: string) => { memoryStore.delete(k) },
    clear: () => { memoryStore.clear() },
    get length() { return memoryStore.size },
    key: (i: number) => [...memoryStore.keys()][i] ?? null,
  }
}

function loadAnalystInfo(): AnalystInfo | null {
  try {
    const raw = getStorage().getItem(ANALYST_KEY)
    return raw ? (JSON.parse(raw) as AnalystInfo) : null
  } catch {
    return null
  }
}

interface AuthState {
  token: string | null
  analyst: AnalystInfo | null
  isAuthenticated: boolean
  isAdmin: boolean
  isSeniorOrAdmin: boolean
  login: (username: string, password: string) => Promise<boolean>
  logout: () => void
  getAuthHeaders: () => Record<string, string>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [token, setToken] = useState<string | null>(() => getStorage().getItem(TOKEN_KEY))
  const [analyst, setAnalyst] = useState<AnalystInfo | null>(() => loadAnalystInfo())

  const isAuthenticated = token !== null
  const isAdmin = analyst?.role === 'admin'
  const isSeniorOrAdmin = analyst?.role === 'senior_analyst' || analyst?.role === 'admin'

  // Cross-tab sync
  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key === TOKEN_KEY) {
        setToken(e.newValue)
        if (!e.newValue) {
          setAnalyst(null)
        } else {
          setAnalyst(loadAnalystInfo())
        }
      }
      if (e.key === ANALYST_KEY) {
        setAnalyst(loadAnalystInfo())
      }
    }
    window.addEventListener('storage', handler)
    return () => window.removeEventListener('storage', handler)
  }, [])

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
        getStorage().setItem(TOKEN_KEY, data.token)
        if (data.analyst) {
          getStorage().setItem(ANALYST_KEY, JSON.stringify(data.analyst))
        }
        setToken(data.token)
        if (data.analyst) {
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
    getStorage().removeItem(TOKEN_KEY)
    getStorage().removeItem(ANALYST_KEY)
    setToken(null)
    setAnalyst(null)
  }, [])

  const getAuthHeaders = useCallback((): Record<string, string> => {
    return token ? { Authorization: `Bearer ${token}` } : {}
  }, [token])

  return (
    <AuthContext.Provider value={{ token, analyst, isAuthenticated, isAdmin, isSeniorOrAdmin, login, logout, getAuthHeaders }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
