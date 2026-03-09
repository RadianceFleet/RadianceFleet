import { describe, it, expect, afterEach } from 'vitest'
import { screen } from '@testing-library/react'
import { AppLayout } from '../components/layout/AppLayout'
import { renderWithProviders } from './testUtils'
import { getStorage } from '../hooks/useAuth'

afterEach(() => {
  getStorage().clear()
})

describe('AppLayout', () => {
  it('renders the app name', () => {
    renderWithProviders(<AppLayout />)
    expect(screen.getByText('RadianceFleet')).toBeInTheDocument()
  })

  it('renders public navigation links when unauthenticated', () => {
    renderWithProviders(<AppLayout />)
    const expectedLabels = [
      'Dashboard', 'Alerts', 'Vessels', 'Map', 'STS Events',
      'Dark Vessels', 'Corridors', 'Watchlist', 'Fleet',
      'Ownership', 'Merges', 'Detect', 'Support',
    ]
    for (const label of expectedLabels) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    // Auth-gated items should not be visible
    expect(screen.queryByText('Ingest')).not.toBeInTheDocument()
    expect(screen.queryByText('Tips')).not.toBeInTheDocument()
  })

  it('shows all nav items when authenticated', () => {
    renderWithProviders(<AppLayout />, { auth: { role: 'admin' } })
    expect(screen.getByText('Ingest')).toBeInTheDocument()
    expect(screen.getByText('Tips')).toBeInTheDocument()
  })

  it('shows login button when unauthenticated', () => {
    renderWithProviders(<AppLayout />)
    expect(screen.getByText('Log in')).toBeInTheDocument()
  })

  it('shows logout button when authenticated', () => {
    renderWithProviders(<AppLayout />, { auth: { role: 'analyst' } })
    expect(screen.getByText('Log out')).toBeInTheDocument()
  })

  it('renders the disclaimer', () => {
    renderWithProviders(<AppLayout />)
    expect(screen.getByText(/Investigative triage only/)).toBeInTheDocument()
  })
})
