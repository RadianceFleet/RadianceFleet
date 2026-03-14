import { describe, it, expect } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { ToastProvider, useToast } from '../components/ui/Toast'
import type { ReactNode } from 'react'

function wrapper({ children }: { children: ReactNode }) {
  return <ToastProvider>{children}</ToastProvider>
}

describe('useToast', () => {
  it('throws if used outside ToastProvider', () => {
    expect(() => {
      renderHook(() => useToast())
    }).toThrow('useToast must be used within <ToastProvider>')
  })

  it('provides addToast function', () => {
    const { result } = renderHook(() => useToast(), { wrapper })
    expect(typeof result.current.addToast).toBe('function')
  })

  it('addToast does not throw', () => {
    const { result } = renderHook(() => useToast(), { wrapper })
    act(() => {
      expect(() => result.current.addToast('Test message', 'success')).not.toThrow()
    })
  })

  it('addToast accepts different types', () => {
    const { result } = renderHook(() => useToast(), { wrapper })
    act(() => {
      expect(() => result.current.addToast('Info', 'info')).not.toThrow()
      expect(() => result.current.addToast('Error', 'error')).not.toThrow()
      expect(() => result.current.addToast('Success', 'success')).not.toThrow()
    })
  })
})
