import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EmptyState } from '../components/ui/EmptyState'

describe('EmptyState', () => {
  it('renders title', () => {
    render(<EmptyState title="No results" />)
    expect(screen.getByText('No results')).toBeInTheDocument()
  })

  it('renders description when provided', () => {
    render(<EmptyState title="No alerts" description="Try adjusting your filters." />)
    expect(screen.getByText('No alerts')).toBeInTheDocument()
    expect(screen.getByText('Try adjusting your filters.')).toBeInTheDocument()
  })

  it('does not render description when not provided', () => {
    render(<EmptyState title="Empty" />)
    expect(screen.queryByText('Try adjusting your filters.')).not.toBeInTheDocument()
  })
})
