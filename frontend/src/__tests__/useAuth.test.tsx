import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { AuthProvider, useAuth, getStorage } from '../hooks/useAuth'
import type { ReactNode } from 'react'

function wrapper({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>
}

afterEach(() => {
  getStorage().clear()
  vi.restoreAllMocks()
})

describe('useAuth', () => {
  it('throws if used outside AuthProvider', () => {
    expect(() => {
      renderHook(() => useAuth())
    }).toThrow('useAuth must be used within AuthProvider')
  })

  it('starts unauthenticated when storage is empty', () => {
    const { result } = renderHook(() => useAuth(), { wrapper })
    expect(result.current.isAuthenticated).toBe(false)
    expect(result.current.token).toBeNull()
    expect(result.current.analyst).toBeNull()
    expect(result.current.isAdmin).toBe(false)
    expect(result.current.isSeniorOrAdmin).toBe(false)
  })

  it('restores auth state from storage', () => {
    getStorage().setItem('rf_admin_token', 'existing-token')
    getStorage().setItem(
      'rf_analyst_info',
      JSON.stringify({
        analyst_id: 1,
        username: 'admin',
        display_name: 'Admin User',
        role: 'admin',
      }),
    )

    const { result } = renderHook(() => useAuth(), { wrapper })
    expect(result.current.isAuthenticated).toBe(true)
    expect(result.current.token).toBe('existing-token')
    expect(result.current.isAdmin).toBe(true)
    expect(result.current.isSeniorOrAdmin).toBe(true)
    expect(result.current.analyst?.username).toBe('admin')
  })

  it('identifies senior_analyst as isSeniorOrAdmin', () => {
    getStorage().setItem('rf_admin_token', 'token')
    getStorage().setItem(
      'rf_analyst_info',
      JSON.stringify({
        analyst_id: 2,
        username: 'senior',
        display_name: 'Senior',
        role: 'senior_analyst',
      }),
    )

    const { result } = renderHook(() => useAuth(), { wrapper })
    expect(result.current.isAdmin).toBe(false)
    expect(result.current.isSeniorOrAdmin).toBe(true)
  })

  it('identifies analyst as not admin and not senior', () => {
    getStorage().setItem('rf_admin_token', 'token')
    getStorage().setItem(
      'rf_analyst_info',
      JSON.stringify({
        analyst_id: 3,
        username: 'analyst',
        display_name: 'Analyst',
        role: 'analyst',
      }),
    )

    const { result } = renderHook(() => useAuth(), { wrapper })
    expect(result.current.isAdmin).toBe(false)
    expect(result.current.isSeniorOrAdmin).toBe(false)
  })

  it('login stores token and analyst on success', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          token: 'new-jwt-token',
          analyst: {
            analyst_id: 1,
            username: 'admin',
            display_name: 'Admin',
            role: 'admin',
          },
        }),
    } as Response)

    const { result } = renderHook(() => useAuth(), { wrapper })

    let loginResult: boolean
    await act(async () => {
      loginResult = await result.current.login('admin', 'password')
    })

    expect(loginResult!).toBe(true)
    expect(result.current.isAuthenticated).toBe(true)
    expect(result.current.token).toBe('new-jwt-token')
    expect(getStorage().getItem('rf_admin_token')).toBe('new-jwt-token')
  })

  it('login returns false on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({ detail: 'Bad credentials' }),
    } as Response)

    const { result } = renderHook(() => useAuth(), { wrapper })

    let loginResult: boolean
    await act(async () => {
      loginResult = await result.current.login('admin', 'wrong')
    })

    expect(loginResult!).toBe(false)
    expect(result.current.isAuthenticated).toBe(false)
  })

  it('login returns false on network error', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('Network error'))

    const { result } = renderHook(() => useAuth(), { wrapper })

    let loginResult: boolean
    await act(async () => {
      loginResult = await result.current.login('admin', 'pass')
    })

    expect(loginResult!).toBe(false)
  })

  it('logout clears auth state and storage', async () => {
    getStorage().setItem('rf_admin_token', 'token')
    getStorage().setItem('rf_analyst_info', JSON.stringify({ analyst_id: 1, username: 'a', display_name: 'A', role: 'admin' }))

    const { result } = renderHook(() => useAuth(), { wrapper })
    expect(result.current.isAuthenticated).toBe(true)

    act(() => {
      result.current.logout()
    })

    expect(result.current.isAuthenticated).toBe(false)
    expect(result.current.token).toBeNull()
    expect(result.current.analyst).toBeNull()
    expect(getStorage().getItem('rf_admin_token')).toBeNull()
    expect(getStorage().getItem('rf_analyst_info')).toBeNull()
  })

  it('getAuthHeaders returns Bearer header when authenticated', () => {
    getStorage().setItem('rf_admin_token', 'my-token')

    const { result } = renderHook(() => useAuth(), { wrapper })
    expect(result.current.getAuthHeaders()).toEqual({
      Authorization: 'Bearer my-token',
    })
  })

  it('getAuthHeaders returns empty object when unauthenticated', () => {
    const { result } = renderHook(() => useAuth(), { wrapper })
    expect(result.current.getAuthHeaders()).toEqual({})
  })
})
