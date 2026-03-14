import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { VerificationBadge } from '../components/VerificationBadge'

describe('VerificationBadge', () => {
  it('renders "Verified" when verifiedBy is provided', () => {
    render(<VerificationBadge verifiedBy="analyst1" verifiedAt="2026-03-10T12:00:00" />)
    expect(screen.getByText('Verified')).toBeInTheDocument()
  })

  it('renders "Unverified" when verifiedBy is null', () => {
    render(<VerificationBadge verifiedBy={null} verifiedAt={null} />)
    expect(screen.getByText('Unverified')).toBeInTheDocument()
  })

  it('renders "Unverified" when verifiedBy is undefined', () => {
    render(<VerificationBadge />)
    expect(screen.getByText('Unverified')).toBeInTheDocument()
  })

  it('shows verification details in title when verified', () => {
    render(<VerificationBadge verifiedBy="analyst1" verifiedAt="2026-03-10T12:00:00" />)
    const badge = screen.getByText('Verified')
    expect(badge.title).toContain('analyst1')
    expect(badge.title).toContain('2026-03-10')
  })

  it('shows "Not verified" in title when unverified', () => {
    render(<VerificationBadge verifiedBy={null} />)
    const badge = screen.getByText('Unverified')
    expect(badge.title).toBe('Not verified')
  })

  it('handles verifiedBy with no verifiedAt', () => {
    render(<VerificationBadge verifiedBy="analyst2" />)
    const badge = screen.getByText('Verified')
    expect(badge.title).toBe('Verified by analyst2')
  })
})
