import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScoreBadge } from '../components/ui/ScoreBadge'

describe('ScoreBadge', () => {
  it('renders the score value', () => {
    render(<ScoreBadge score={85} />)
    expect(screen.getByText('85')).toBeInTheDocument()
  })

  it('renders low score', () => {
    render(<ScoreBadge score={10} />)
    expect(screen.getByText('10')).toBeInTheDocument()
  })

  it('renders score of 0', () => {
    render(<ScoreBadge score={0} />)
    expect(screen.getByText('0')).toBeInTheDocument()
  })

  it('renders score of 100', () => {
    render(<ScoreBadge score={100} />)
    expect(screen.getByText('100')).toBeInTheDocument()
  })
})
