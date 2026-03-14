import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StatusBadge } from '../components/ui/StatusBadge'

describe('StatusBadge', () => {
  it('renders the status text with underscores replaced by spaces', () => {
    render(<StatusBadge status="under_review" />)
    expect(screen.getByText('under review')).toBeInTheDocument()
  })

  it('renders single-word status as-is', () => {
    render(<StatusBadge status="new" />)
    expect(screen.getByText('new')).toBeInTheDocument()
  })

  it('renders multi-underscore status', () => {
    render(<StatusBadge status="needs_satellite_check" />)
    expect(screen.getByText('needs satellite check')).toBeInTheDocument()
  })

  it('renders documented status', () => {
    render(<StatusBadge status="documented" />)
    expect(screen.getByText('documented')).toBeInTheDocument()
  })

  it('handles unknown status gracefully', () => {
    render(<StatusBadge status="custom_status" />)
    expect(screen.getByText('custom status')).toBeInTheDocument()
  })
})
