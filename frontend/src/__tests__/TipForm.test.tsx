import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { TipForm } from '../components/TipForm'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('TipForm', () => {
  it('renders the "Flag this vessel" button initially', () => {
    render(<TipForm mmsi="123456789" vesselName="SHADOW TANKER" />)
    expect(screen.getByText('Flag this vessel')).toBeInTheDocument()
  })

  it('opens the form when "Flag this vessel" is clicked', async () => {
    const user = userEvent.setup()
    render(<TipForm mmsi="123456789" vesselName="SHADOW TANKER" />)

    await user.click(screen.getByText('Flag this vessel'))

    expect(screen.getByText(/Flag suspicious behavior for SHADOW TANKER/)).toBeInTheDocument()
    expect(screen.getByText('Submit tip')).toBeInTheDocument()
    expect(screen.getByText('Cancel')).toBeInTheDocument()
  })

  it('closes the form when Cancel is clicked', async () => {
    const user = userEvent.setup()
    render(<TipForm mmsi="123456789" vesselName="TEST VESSEL" />)

    await user.click(screen.getByText('Flag this vessel'))
    expect(screen.getByText('Submit tip')).toBeInTheDocument()

    await user.click(screen.getByText('Cancel'))
    expect(screen.getByText('Flag this vessel')).toBeInTheDocument()
  })

  it('shows error when no behavior type is selected', async () => {
    const user = userEvent.setup()
    render(<TipForm mmsi="123456789" vesselName="TEST VESSEL" />)

    await user.click(screen.getByText('Flag this vessel'))
    await user.click(screen.getByText('Submit tip'))

    expect(screen.getByText('Please select a behavior type.')).toBeInTheDocument()
  })

  it('shows error when detail text is too short', async () => {
    const user = userEvent.setup()
    render(<TipForm mmsi="123456789" vesselName="TEST VESSEL" />)

    await user.click(screen.getByText('Flag this vessel'))

    // Select a behavior type
    await user.selectOptions(
      screen.getByDisplayValue('Select behavior type...'),
      'AIS_MANIPULATION',
    )

    // Type short detail text
    await user.type(
      screen.getByPlaceholderText(/Describe the suspicious behavior/),
      'Too short',
    )
    await user.click(screen.getByText('Submit tip'))

    expect(
      screen.getByText('Please provide at least 50 characters of detail.'),
    ).toBeInTheDocument()
  })

  it('shows error when detail text exceeds 500 characters', async () => {
    const user = userEvent.setup()
    render(<TipForm mmsi="123456789" vesselName="TEST VESSEL" />)

    await user.click(screen.getByText('Flag this vessel'))

    await user.selectOptions(
      screen.getByDisplayValue('Select behavior type...'),
      'DARK_PERIOD',
    )

    const longText = 'A'.repeat(501)
    await user.type(screen.getByPlaceholderText(/Describe the suspicious behavior/), longText)
    await user.click(screen.getByText('Submit tip'))

    expect(screen.getByText('Detail text must be 500 characters or less.')).toBeInTheDocument()
  })

  it('shows character counter', async () => {
    const user = userEvent.setup()
    render(<TipForm mmsi="123456789" vesselName="TEST VESSEL" />)

    await user.click(screen.getByText('Flag this vessel'))
    expect(screen.getByText('0/500')).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText(/Describe the suspicious behavior/), 'Hello')
    expect(screen.getByText('5/500')).toBeInTheDocument()
  })

  it('shows success message after successful submission', async () => {
    const user = userEvent.setup()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ id: 1 }),
    } as Response)

    render(<TipForm mmsi="123456789" vesselName="TEST VESSEL" />)

    await user.click(screen.getByText('Flag this vessel'))

    await user.selectOptions(
      screen.getByDisplayValue('Select behavior type...'),
      'AIS_MANIPULATION',
    )

    const validDetail = 'This vessel has been observed manipulating its AIS transponder repeatedly in the Baltic Sea region.'
    await user.type(screen.getByPlaceholderText(/Describe the suspicious behavior/), validDetail)
    await user.click(screen.getByText('Submit tip'))

    await waitFor(() => {
      expect(screen.getByText(/Thank you/)).toBeInTheDocument()
    })
  })

  it('shows network error on fetch failure', async () => {
    const user = userEvent.setup()
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('Network error'))

    render(<TipForm mmsi="123456789" vesselName="TEST VESSEL" />)

    await user.click(screen.getByText('Flag this vessel'))

    await user.selectOptions(
      screen.getByDisplayValue('Select behavior type...'),
      'SUSPICIOUS_STS',
    )

    const validDetail = 'Suspicious ship-to-ship transfer observed near the coast of Malaysia with unidentified vessel.'
    await user.type(screen.getByPlaceholderText(/Describe the suspicious behavior/), validDetail)
    await user.click(screen.getByText('Submit tip'))

    await waitFor(() => {
      expect(screen.getByText('Network error. Please try again.')).toBeInTheDocument()
    })
  })
})
