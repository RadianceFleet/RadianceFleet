import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ApiError, apiFetch } from '../lib/api'
import { getStorage } from '../hooks/useAuth'

describe('ApiError', () => {
  it('stores status and detail', () => {
    const err = new ApiError(404, 'Not found')
    expect(err.status).toBe(404)
    expect(err.detail).toBe('Not found')
    expect(err.message).toBe('Not found')
    expect(err.name).toBe('ApiError')
  })

  it('is an instance of Error', () => {
    const err = new ApiError(500, 'Server error')
    expect(err).toBeInstanceOf(Error)
  })
})

describe('apiFetch', () => {
  beforeEach(() => {
    getStorage().clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('makes a request to the correct URL with JSON content type', async () => {
    const mockResponse = { items: [] }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockResponse),
    } as Response)

    const result = await apiFetch('/vessels')
    expect(result).toEqual(mockResponse)
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/vessels',
      expect.objectContaining({
        headers: expect.objectContaining({
          'Content-Type': 'application/json',
        }),
      }),
    )
  })

  it('includes Authorization header when token is stored', async () => {
    getStorage().setItem('rf_admin_token', 'my-jwt-token')

    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({}),
    } as Response)

    await apiFetch('/stats')
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/stats',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer my-jwt-token',
        }),
      }),
    )
  })

  it('does not include Authorization header when no token', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({}),
    } as Response)

    await apiFetch('/stats')
    const call = vi.mocked(fetch).mock.calls[0]
    const headers = call[1]?.headers as Record<string, string>
    expect(headers['Authorization']).toBeUndefined()
  })

  it('throws ApiError on non-ok response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 403,
      statusText: 'Forbidden',
      json: () => Promise.resolve({ detail: 'Access denied' }),
    } as unknown as Response)

    await expect(apiFetch('/admin/stuff')).rejects.toThrow(ApiError)
    try {
      await apiFetch('/admin/stuff')
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError)
      expect((e as ApiError).status).toBe(403)
      expect((e as ApiError).detail).toBe('Access denied')
    }
  })

  it('falls back to statusText when response body is not JSON', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 502,
      statusText: 'Bad Gateway',
      json: () => Promise.reject(new Error('not json')),
    } as unknown as Response)

    try {
      await apiFetch('/bad')
    } catch (e) {
      expect((e as ApiError).detail).toBe('Bad Gateway')
    }
  })

  it('passes through custom init options', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    } as Response)

    await apiFetch('/alerts/1/export', { method: 'POST' })
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/alerts/1/export',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('does not set Content-Type header when body is FormData', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ ok: true }),
    } as Response)

    const formData = new FormData()
    formData.append('file', new Blob(['test']), 'test.csv')

    await apiFetch('/ingestion/upload', { method: 'POST', body: formData })
    const call = vi.mocked(fetch).mock.calls[0]
    const headers = call[1]?.headers as Record<string, string>
    expect(headers['Content-Type']).toBeUndefined()
  })
})
