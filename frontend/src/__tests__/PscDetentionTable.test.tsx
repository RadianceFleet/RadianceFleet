import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PscDetentionTable from '../components/PscDetentionTable'

const mockDetentions = [
  {
    psc_detention_id: 1,
    detention_date: '2025-06-15',
    mou_source: 'Paris MOU',
    port_name: 'Rotterdam',
    port_country: 'Netherlands',
    deficiency_count: 5,
    major_deficiency_count: 2,
    detention_reason: 'Fire safety deficiencies',
  },
  {
    psc_detention_id: 2,
    detention_date: '2024-11-20',
    mou_source: 'Tokyo MOU',
    port_name: null,
    port_country: null,
    deficiency_count: 3,
    major_deficiency_count: 0,
    detention_reason: null,
  },
]

describe('PscDetentionTable', () => {
  it('renders "No PSC detentions on record" when empty', () => {
    render(<PscDetentionTable detentions={[]} />)
    expect(screen.getByText('No PSC detentions on record')).toBeInTheDocument()
  })

  it('renders the collapse/expand button with detention count', () => {
    render(<PscDetentionTable detentions={mockDetentions} />)
    expect(screen.getByText(/PSC Detentions \(2\)/)).toBeInTheDocument()
  })

  it('uses detentionCount prop when provided', () => {
    render(<PscDetentionTable detentions={mockDetentions} detentionCount={10} />)
    expect(screen.getByText(/PSC Detentions \(10\)/)).toBeInTheDocument()
  })

  it('shows latest date when provided', () => {
    render(<PscDetentionTable detentions={mockDetentions} latestDate="2025-06-15" />)
    expect(screen.getByText(/Latest: 2025-06-15/)).toBeInTheDocument()
  })

  it('does not show table until expanded', () => {
    render(<PscDetentionTable detentions={mockDetentions} />)
    expect(screen.queryByText('Rotterdam')).not.toBeInTheDocument()
  })

  it('shows table rows after expanding', async () => {
    const user = userEvent.setup()
    render(<PscDetentionTable detentions={mockDetentions} />)

    await user.click(screen.getByText(/PSC Detentions/))

    expect(screen.getByText('Rotterdam')).toBeInTheDocument()
    expect(screen.getByText('Paris MOU')).toBeInTheDocument()
    expect(screen.getByText('Tokyo MOU')).toBeInTheDocument()
    expect(screen.getByText('Fire safety deficiencies')).toBeInTheDocument()
    expect(screen.getByText('5 (2 major)')).toBeInTheDocument()
  })

  it('collapses the table on second click', async () => {
    const user = userEvent.setup()
    render(<PscDetentionTable detentions={mockDetentions} />)

    await user.click(screen.getByText(/PSC Detentions/))
    expect(screen.getByText('Rotterdam')).toBeInTheDocument()

    await user.click(screen.getByText(/PSC Detentions/))
    expect(screen.queryByText('Rotterdam')).not.toBeInTheDocument()
  })
})
