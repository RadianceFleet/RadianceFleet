import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ScoreBreakdown } from '../components/ScoreBreakdown'

describe('ScoreBreakdown', () => {
  const breakdown = {
    dark_zone: 15,
    speed_anomaly: 10,
    flag_legitimacy: -5,
    _total: 20,
    non_numeric: 'ignored',
  }

  it('renders risk signals with positive values', () => {
    render(<ScoreBreakdown breakdown={breakdown} />)
    expect(screen.getByText('Risk Signals')).toBeInTheDocument()
    expect(screen.getByText('Dark Zone')).toBeInTheDocument()
    expect(screen.getByText('Speed Anomaly')).toBeInTheDocument()
  })

  it('renders legitimacy signals with negative values', () => {
    render(<ScoreBreakdown breakdown={breakdown} />)
    expect(screen.getByText('Legitimacy Signals')).toBeInTheDocument()
    expect(screen.getByText('Flag Legitimacy')).toBeInTheDocument()
  })

  it('renders positive values with + prefix', () => {
    render(<ScoreBreakdown breakdown={breakdown} />)
    expect(screen.getByText('+15')).toBeInTheDocument()
    expect(screen.getByText('+10')).toBeInTheDocument()
  })

  it('renders negative values as-is', () => {
    render(<ScoreBreakdown breakdown={breakdown} />)
    expect(screen.getByText('-5')).toBeInTheDocument()
  })

  it('renders metadata entries (keys starting with _)', () => {
    render(<ScoreBreakdown breakdown={breakdown} />)
    expect(screen.getByText('Total:')).toBeInTheDocument()
  })

  it('toggles raw JSON display on button click', async () => {
    const user = userEvent.setup()
    render(<ScoreBreakdown breakdown={breakdown} />)

    expect(screen.getByText('Show raw JSON')).toBeInTheDocument()
    expect(screen.queryByText(/"dark_zone"/)).not.toBeInTheDocument()

    await user.click(screen.getByText('Show raw JSON'))
    expect(screen.getByText('Hide raw JSON')).toBeInTheDocument()
    // JSON content should now be visible
    expect(screen.getByText(/dark_zone/)).toBeInTheDocument()

    await user.click(screen.getByText('Hide raw JSON'))
    expect(screen.getByText('Show raw JSON')).toBeInTheDocument()
  })

  it('handles breakdown with only positive signals', () => {
    render(<ScoreBreakdown breakdown={{ anomaly: 10 }} />)
    expect(screen.getByText('Risk Signals')).toBeInTheDocument()
    expect(screen.queryByText('Legitimacy Signals')).not.toBeInTheDocument()
  })

  it('handles breakdown with only negative signals', () => {
    render(<ScoreBreakdown breakdown={{ flag_ok: -3 }} />)
    expect(screen.queryByText('Risk Signals')).not.toBeInTheDocument()
    expect(screen.getByText('Legitimacy Signals')).toBeInTheDocument()
  })

  it('handles empty breakdown', () => {
    const { container } = render(<ScoreBreakdown breakdown={{}} />)
    expect(container.querySelector('div')).toBeInTheDocument()
    expect(screen.queryByText('Risk Signals')).not.toBeInTheDocument()
    expect(screen.queryByText('Legitimacy Signals')).not.toBeInTheDocument()
  })
})
