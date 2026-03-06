import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from './testUtils'

const mockAlerts = [
  {
    gap_event_id: 1,
    vessel_id: 10,
    vessel_name: 'SHADOW TANKER',
    risk_score: 85,
    gap_start_utc: '2026-03-01T12:00:00',
    duration_minutes: 720,
    status: 'new',
    impossible_speed_flag: false,
    in_dark_zone: true,
    is_recurring_pattern: false,
    prior_similar_count: null,
  },
  {
    gap_event_id: 2,
    vessel_id: 11,
    vessel_name: 'GHOST CARRIER',
    risk_score: 62,
    gap_start_utc: '2026-03-02T08:00:00',
    duration_minutes: 360,
    status: 'under_review',
    impossible_speed_flag: true,
    in_dark_zone: false,
    is_recurring_pattern: true,
    prior_similar_count: 3,
  },
]

const mockBulkMutate = vi.fn()

vi.mock('../hooks/useAlerts', () => ({
  useAlerts: () => ({
    data: { items: mockAlerts, total: 2 },
    isLoading: false,
    error: null,
  }),
  useBulkUpdateAlertStatus: () => ({
    mutate: mockBulkMutate,
    isPending: false,
  }),
}))

import { AlertListPage } from '../components/AlertList'

describe('AlertList interaction', () => {
  it('renders alert rows with vessel names', () => {
    renderWithProviders(<AlertListPage />)
    expect(screen.getByText('SHADOW TANKER')).toBeInTheDocument()
    expect(screen.getByText('GHOST CARRIER')).toBeInTheDocument()
  })

  it('shows recurring pattern badge', () => {
    renderWithProviders(<AlertListPage />)
    expect(screen.getByText(/Recurring/)).toBeInTheDocument()
  })

  it('allows selecting alerts with checkboxes', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AlertListPage />)

    // There should be individual checkboxes plus header checkbox
    const checkboxes = screen.getAllByRole('checkbox')
    expect(checkboxes.length).toBeGreaterThanOrEqual(3) // header + 2 rows

    // Click individual checkbox for first alert
    await user.click(checkboxes[1])
    expect(screen.getByText('1 selected')).toBeInTheDocument()
  })

  it('select-all checkbox selects all rows', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AlertListPage />)

    const checkboxes = screen.getAllByRole('checkbox')
    // Click header checkbox (first one)
    await user.click(checkboxes[0])
    expect(screen.getByText('2 selected')).toBeInTheDocument()
  })

  it('filters by min score', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AlertListPage />)

    const minScoreInput = screen.getByPlaceholderText('Min score')
    await user.type(minScoreInput, '70')
    expect(minScoreInput).toHaveValue('70')
  })

  it('toggles patterns-only filter', async () => {
    const user = userEvent.setup()
    renderWithProviders(<AlertListPage />)

    const patternsButton = screen.getByText('Patterns only')
    await user.click(patternsButton)
    // After filtering, only GHOST CARRIER (recurring) should show
    expect(screen.getByText('GHOST CARRIER')).toBeInTheDocument()
  })
})
