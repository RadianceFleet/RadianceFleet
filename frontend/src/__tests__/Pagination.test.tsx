import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Pagination } from '../components/ui/Pagination'

describe('Pagination', () => {
  it('renders page info and total count', () => {
    render(<Pagination page={0} totalPages={5} total={100} onPageChange={vi.fn()} />)
    expect(screen.getByText('100 items')).toBeInTheDocument()
    expect(screen.getByText('Page 1 of 5')).toBeInTheDocument()
  })

  it('uses custom label', () => {
    render(
      <Pagination page={0} totalPages={3} total={42} onPageChange={vi.fn()} label="alerts" />,
    )
    expect(screen.getByText('42 alerts')).toBeInTheDocument()
  })

  it('disables Prev button on first page', () => {
    render(<Pagination page={0} totalPages={5} total={100} onPageChange={vi.fn()} />)
    expect(screen.getByText('Prev')).toBeDisabled()
  })

  it('disables Next button on last page', () => {
    render(<Pagination page={4} totalPages={5} total={100} onPageChange={vi.fn()} />)
    expect(screen.getByText('Next')).toBeDisabled()
  })

  it('enables both buttons on middle page', () => {
    render(<Pagination page={2} totalPages={5} total={100} onPageChange={vi.fn()} />)
    expect(screen.getByText('Prev')).not.toBeDisabled()
    expect(screen.getByText('Next')).not.toBeDisabled()
  })

  it('calls onPageChange with next page', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Pagination page={1} totalPages={5} total={100} onPageChange={onChange} />)
    await user.click(screen.getByText('Next'))
    expect(onChange).toHaveBeenCalledWith(2)
  })

  it('calls onPageChange with previous page', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Pagination page={2} totalPages={5} total={100} onPageChange={onChange} />)
    await user.click(screen.getByText('Prev'))
    expect(onChange).toHaveBeenCalledWith(1)
  })

  it('does not go below page 0', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Pagination page={0} totalPages={5} total={100} onPageChange={onChange} />)
    // Prev is disabled so clicking should not trigger
    await user.click(screen.getByText('Prev'))
    expect(onChange).not.toHaveBeenCalled()
  })

  it('does not go above last page', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Pagination page={4} totalPages={5} total={100} onPageChange={onChange} />)
    await user.click(screen.getByText('Next'))
    expect(onChange).not.toHaveBeenCalled()
  })
})
