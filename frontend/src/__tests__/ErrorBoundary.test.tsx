import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ErrorBoundary } from '../components/ErrorBoundary'

function ThrowingChild({ error }: { error: Error }) {
  throw error
}

describe('ErrorBoundary', () => {
  // Suppress React's error boundary console output during tests
  const originalConsoleError = console.error
  beforeEach(() => {
    console.error = vi.fn()
  })
  afterEach(() => {
    console.error = originalConsoleError
  })

  it('renders children when no error', () => {
    render(
      <ErrorBoundary>
        <div>Normal content</div>
      </ErrorBoundary>,
    )
    expect(screen.getByText('Normal content')).toBeInTheDocument()
  })

  it('renders error message when child throws', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild error={new Error('Test crash')} />
      </ErrorBoundary>,
    )
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
    expect(screen.getByText('Test crash')).toBeInTheDocument()
  })

  it('renders a Reload button', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild error={new Error('Boom')} />
      </ErrorBoundary>,
    )
    expect(screen.getByText('Reload')).toBeInTheDocument()
  })
})
