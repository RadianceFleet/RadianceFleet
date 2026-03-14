import { describe, it, expect } from 'vitest'
import { buildQueryParams } from '../utils/queryParams'

describe('buildQueryParams', () => {
  it('builds params from a simple record', () => {
    const result = buildQueryParams({ search: 'tanker', limit: 20 })
    expect(result.get('search')).toBe('tanker')
    expect(result.get('limit')).toBe('20')
  })

  it('skips undefined values', () => {
    const result = buildQueryParams({ search: undefined, limit: 10 })
    expect(result.has('search')).toBe(false)
    expect(result.get('limit')).toBe('10')
  })

  it('skips null values', () => {
    const result = buildQueryParams({ flag: null, limit: 5 })
    expect(result.has('flag')).toBe(false)
    expect(result.get('limit')).toBe('5')
  })

  it('skips empty string values', () => {
    const result = buildQueryParams({ search: '', limit: 10 })
    expect(result.has('search')).toBe(false)
  })

  it('converts boolean values to strings', () => {
    const result = buildQueryParams({ watchlist_only: true, active: false })
    expect(result.get('watchlist_only')).toBe('true')
    expect(result.get('active')).toBe('false')
  })

  it('converts number values to strings', () => {
    const result = buildQueryParams({ min_dwt: 50000, skip: 0 })
    expect(result.get('min_dwt')).toBe('50000')
    expect(result.get('skip')).toBe('0')
  })

  it('returns empty params for all-undefined record', () => {
    const result = buildQueryParams({ a: undefined, b: null, c: '' })
    expect(result.toString()).toBe('')
  })

  it('returns empty params for empty record', () => {
    const result = buildQueryParams({})
    expect(result.toString()).toBe('')
  })
})
