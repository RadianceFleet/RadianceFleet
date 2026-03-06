import { describe, it, expect } from 'vitest'
import { screen } from '@testing-library/react'
import { AppLayout } from '../components/layout/AppLayout'
import { renderWithProviders } from './testUtils'

describe('AppLayout', () => {
  it('renders the app name', () => {
    renderWithProviders(<AppLayout />)
    expect(screen.getByText('RadianceFleet')).toBeInTheDocument()
  })

  it('renders all navigation links', () => {
    renderWithProviders(<AppLayout />)
    const expectedLabels = [
      'Dashboard', 'Alerts', 'Vessels', 'Map', 'STS Events',
      'Dark Vessels', 'Corridors', 'Watchlist', 'Fleet',
      'Ownership', 'Merges', 'Ingest', 'Detect', 'Support',
    ]
    for (const label of expectedLabels) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it('renders the disclaimer', () => {
    renderWithProviders(<AppLayout />)
    expect(screen.getByText(/Investigative triage only/)).toBeInTheDocument()
  })
})
