import { describe, it, expect, vi, afterEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { renderWithProviders } from './testUtils'
import { LoginModal } from '../components/LoginModal'
import { getStorage } from '../hooks/useAuth'

afterEach(() => {
  getStorage().clear()
  vi.restoreAllMocks()
})

describe('LoginModal', () => {
  it('renders the login form', () => {
    renderWithProviders(<LoginModal onSuccess={vi.fn()} onClose={vi.fn()} />)
    expect(screen.getByText('Admin Login')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Username')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Password')).toBeInTheDocument()
    expect(screen.getByText('Login')).toBeInTheDocument()
    expect(screen.getByText('Cancel')).toBeInTheDocument()
  })

  it('calls onClose when Cancel is clicked', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    renderWithProviders(<LoginModal onSuccess={vi.fn()} onClose={onClose} />)
    await user.click(screen.getByText('Cancel'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('allows typing in username and password fields', async () => {
    const user = userEvent.setup()
    renderWithProviders(<LoginModal onSuccess={vi.fn()} onClose={vi.fn()} />)

    const usernameInput = screen.getByPlaceholderText('Username')
    const passwordInput = screen.getByPlaceholderText('Password')

    await user.type(usernameInput, 'admin')
    await user.type(passwordInput, 'secret')

    expect(usernameInput).toHaveValue('admin')
    expect(passwordInput).toHaveValue('secret')
  })

  it('shows error on failed login', async () => {
    const user = userEvent.setup()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({}),
    } as Response)

    renderWithProviders(<LoginModal onSuccess={vi.fn()} onClose={vi.fn()} />)

    await user.type(screen.getByPlaceholderText('Username'), 'admin')
    await user.type(screen.getByPlaceholderText('Password'), 'wrong')
    await user.click(screen.getByText('Login'))

    await waitFor(() => {
      expect(screen.getByText('Invalid username or password')).toBeInTheDocument()
    })
  })

  it('calls onSuccess on successful login', async () => {
    const user = userEvent.setup()
    const onSuccess = vi.fn()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          token: 'jwt-token-123',
          analyst: { analyst_id: 1, username: 'admin', display_name: 'Admin', role: 'admin' },
        }),
    } as Response)

    renderWithProviders(<LoginModal onSuccess={onSuccess} onClose={vi.fn()} />)

    await user.type(screen.getByPlaceholderText('Username'), 'admin')
    await user.type(screen.getByPlaceholderText('Password'), 'correct')
    await user.click(screen.getByText('Login'))

    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledTimes(1)
    })
  })

  it('clears password after failed login', async () => {
    const user = userEvent.setup()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      json: () => Promise.resolve({}),
    } as Response)

    renderWithProviders(<LoginModal onSuccess={vi.fn()} onClose={vi.fn()} />)

    await user.type(screen.getByPlaceholderText('Username'), 'admin')
    await user.type(screen.getByPlaceholderText('Password'), 'wrong')
    await user.click(screen.getByText('Login'))

    await waitFor(() => {
      expect(screen.getByPlaceholderText('Password')).toHaveValue('')
    })
  })

  it('shows loading state during login', async () => {
    const user = userEvent.setup()
    let resolveLogin: (value: Response) => void
    const loginPromise = new Promise<Response>((resolve) => {
      resolveLogin = resolve
    })
    vi.spyOn(globalThis, 'fetch').mockReturnValue(loginPromise)

    renderWithProviders(<LoginModal onSuccess={vi.fn()} onClose={vi.fn()} />)

    await user.type(screen.getByPlaceholderText('Username'), 'admin')
    await user.type(screen.getByPlaceholderText('Password'), 'pass')
    await user.click(screen.getByText('Login'))

    expect(screen.getByText('Logging in...')).toBeInTheDocument()

    resolveLogin!({
      ok: false,
      json: () => Promise.resolve({}),
    } as Response)

    await waitFor(() => {
      expect(screen.getByText('Login')).toBeInTheDocument()
    })
  })
})
