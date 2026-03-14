import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, renderHook, act } from '@testing-library/react'
import { userEvent } from '@testing-library/user-event'
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

describe('Toast auto-dismiss', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('auto-dismisses after 5 seconds', () => {
    const { result } = renderHook(() => useToast(), { wrapper })

    act(() => {
      result.current.addToast('Disappearing toast', 'info')
    })

    // Re-render the provider to see the toast in the DOM
    const ToastConsumer = () => {
      const { addToast } = useToast()
      return <button onClick={() => addToast('test', 'info')}>add</button>
    }

    const { getByText, queryByText } = render(
      <ToastProvider>
        <ToastConsumer />
      </ToastProvider>
    )

    // Add a toast via the rendered component
    act(() => {
      getByText('add').click()
    })

    expect(getByText('test')).toBeInTheDocument()

    // Advance time by 5 seconds
    act(() => {
      vi.advanceTimersByTime(5000)
    })

    expect(queryByText('test')).not.toBeInTheDocument()
  })

  it('does not dismiss before 5 seconds', () => {
    const ToastConsumer = () => {
      const { addToast } = useToast()
      return <button onClick={() => addToast('still here', 'success')}>add</button>
    }

    const { getByText, queryByText } = render(
      <ToastProvider>
        <ToastConsumer />
      </ToastProvider>
    )

    act(() => {
      getByText('add').click()
    })

    expect(getByText('still here')).toBeInTheDocument()

    // Advance only 4 seconds — toast should still be visible
    act(() => {
      vi.advanceTimersByTime(4000)
    })

    expect(queryByText('still here')).toBeInTheDocument()
  })

  it('dismisses on click', async () => {
    vi.useRealTimers() // Use real timers for user event

    const ToastConsumer = () => {
      const { addToast } = useToast()
      return <button onClick={() => addToast('click me away', 'error')}>add</button>
    }

    const { getByText, queryByText } = render(
      <ToastProvider>
        <ToastConsumer />
      </ToastProvider>
    )

    await userEvent.click(getByText('add'))
    expect(getByText('click me away')).toBeInTheDocument()

    await userEvent.click(getByText('click me away'))
    expect(queryByText('click me away')).not.toBeInTheDocument()
  })
})
