import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiJson } from '../api/client'
import { fakeAuthClient } from '../test-utils'
import { AuthProvider, useAuth } from './AuthProvider'

function Probe() {
  const { ready } = useAuth()
  const q = useQuery({ enabled: ready, queryKey: ['x'], retry: false, queryFn: () => apiJson('/api/v1/me') })
  return <p>{q.isError ? 'failed' : 'loading'}</p>
}

afterEach(() => vi.unstubAllGlobals())

describe('account_deleted', () => {
  it('signs the user out and shows the deletion message on 403 account_deleted', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ code: 'account_deleted' }), { status: 403 })))
    const client = fakeAuthClient()
    render(
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider client={client}>
          <Probe />
        </AuthProvider>
      </QueryClientProvider>,
    )
    expect(await screen.findByText('Your account is being deleted')).toBeInTheDocument()
    expect(client.getSession()).toBeNull()
  })
})
