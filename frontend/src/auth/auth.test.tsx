import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { apiFetch } from '../api/client'
import { fakeAuthClient, mockApi, ME_WITH_RESEARCHER, PROFILE } from '../test-utils'
import { SESSION_STORAGE_KEY, loadStoredSession } from './AuthClient'
import { DevAuthClient } from './DevAuthClient'
import { SupabaseAuthClient } from './SupabaseAuthClient'
import { createAuthClient } from './createAuthClient'
import { guardRedirect } from './guard'

beforeEach(() => {
  localStorage.clear()
  vi.unstubAllGlobals()
})

describe('createAuthClient', () => {
  it('defaults to dev and selects supabase only with full config', () => {
    expect(createAuthClient({})).toBeInstanceOf(DevAuthClient)
    expect(createAuthClient({ VITE_AUTH_MODE: 'dev' })).toBeInstanceOf(DevAuthClient)
    expect(createAuthClient({ VITE_AUTH_MODE: 'supabase', VITE_SUPABASE_URL: 'https://x.supabase.co', VITE_SUPABASE_PUBLISHABLE_KEY: 'k' })).toBeInstanceOf(SupabaseAuthClient)
    expect(() => createAuthClient({ VITE_AUTH_MODE: 'supabase' })).toThrow(/VITE_SUPABASE_URL/)
  })
})

describe('DevAuthClient', () => {
  it('logs in through /dev/login and stores the session', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify({ access_token: 't1', token_type: 'bearer', expires_in: 3600 }), { status: 200 }))
    const c = new DevAuthClient(fetchImpl as unknown as typeof fetch, 'http://api')
    await c.init()
    expect(c.getSession()).toBeNull()
    await c.signInWithEmail('a@example.edu')
    expect(fetchImpl).toHaveBeenCalledWith('http://api/api/v1/dev/login', expect.objectContaining({ method: 'POST', body: JSON.stringify({ email: 'a@example.edu' }) }))
    expect(c.getSession()?.accessToken).toBe('t1')
    expect(loadStoredSession()?.accessToken).toBe('t1')
    await c.signOut()
    expect(localStorage.getItem(SESSION_STORAGE_KEY)).toBeNull()
  })
  it('ignores corrupt or expired stored sessions', () => {
    localStorage.setItem(SESSION_STORAGE_KEY, '{nope')
    expect(loadStoredSession()).toBeNull()
    localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({ accessToken: 'x', expiresAt: 1 }))
    expect(loadStoredSession()).toBeNull()
  })
})

describe('guardRedirect', () => {
  it('routes by auth state', () => {
    expect(guardRedirect({ authenticated: false, hasResearcher: false, pathname: '/' })).toBe('/signin')
    expect(guardRedirect({ authenticated: false, hasResearcher: false, pathname: '/signin' })).toBeNull()
    expect(guardRedirect({ authenticated: true, hasResearcher: false, pathname: '/' })).toBe('/onboarding')
    expect(guardRedirect({ authenticated: true, hasResearcher: false, pathname: '/onboarding' })).toBeNull()
    expect(guardRedirect({ authenticated: true, hasResearcher: true, pathname: '/' })).toBeNull()
    expect(guardRedirect({ authenticated: true, hasResearcher: true, pathname: '/signin' })).toBe('/')
    expect(guardRedirect({ authenticated: true, hasResearcher: false, pathname: '/signin' })).toBe('/onboarding')
  })
})

describe('app routing and token injection', () => {
  it('sends the bearer token and signs out on 401, landing on /signin', async () => {
    const fetchFn = vi.fn(async () => new Response('{}', { status: 401 }))
    vi.stubGlobal('fetch', fetchFn)
    const client = fakeAuthClient()
    render(
      <MemoryRouter initialEntries={['/']}>
        <App authClient={client} />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { name: 'Sign in or create an account' })).toBeInTheDocument()
    const init = (fetchFn.mock.calls[0] as unknown as [string, RequestInit])[1]
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer tok')
    expect(client.getSession()).toBeNull()
  })

  it('sends signed-out visitors to /signin and no token header', async () => {
    const fetchFn = mockApi({})
    render(
      <MemoryRouter initialEntries={['/vault']}>
        <App authClient={fakeAuthClient(null)} />
      </MemoryRouter>,
    )
    expect(await screen.findByRole('heading', { name: 'Sign in or create an account' })).toBeInTheDocument()
    await apiFetch('/x')
    const last = fetchFn.mock.calls.at(-1) as unknown as [string, RequestInit]
    expect(new Headers(last[1].headers).get('Authorization')).toBeNull()
  })

  it('sends an account without researcher to /onboarding with an empty name field', async () => {
    mockApi({ '/api/v1/me': { ...ME_WITH_RESEARCHER, researcher: null } })
    render(
      <MemoryRouter initialEntries={['/']}>
        <App authClient={fakeAuthClient()} />
      </MemoryRouter>,
    )
    const input = await screen.findByLabelText(/Your name/)
    expect(input).toHaveValue('')
  })

  it('shows Home with the profile summary for a claimed researcher', async () => {
    mockApi({ '/api/v1/me': ME_WITH_RESEARCHER, '/api/v1/profile': PROFILE })
    render(
      <MemoryRouter initialEntries={['/signin']}>
        <App authClient={fakeAuthClient()} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('Ada Test')).toBeInTheDocument()
    expect(screen.getByText(/of 4 works included/)).toBeInTheDocument()
  })

  it('dev sign-in form submits the e-mail', async () => {
    const client = fakeAuthClient(null)
    const spy = vi.spyOn(client, 'signInWithEmail')
    mockApi({})
    render(
      <MemoryRouter initialEntries={['/signin']}>
        <App authClient={client} />
      </MemoryRouter>,
    )
    await userEvent.type(await screen.findByLabelText('Work email'), 'a@example.edu')
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await waitFor(() => expect(spy).toHaveBeenCalledWith('a@example.edu'))
    expect(screen.queryByRole('button', { name: /ORCID/ })).toBeNull()
  })
})
