import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../auth/AuthProvider'
import { fakeAuthClient } from '../../test-utils'
import { NotificationSettings } from './NotificationSettings'

const INITIAL = { email_runs: true, email_matches: true, email_digest: false }

function stub(patch: (body: Record<string, boolean>) => Response) {
  const patches: Record<string, boolean>[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        const body = JSON.parse(init.body as string)
        patches.push(body)
        return patch(body)
      }
      return new Response(JSON.stringify(INITIAL), { status: 200 })
    }),
  )
  return patches
}

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <AuthProvider client={fakeAuthClient()}>
        <NotificationSettings />
      </AuthProvider>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('NotificationSettings', () => {
  it('shows the server state and the helper text', async () => {
    stub((b) => new Response(JSON.stringify({ ...INITIAL, ...b })))
    renderIt()
    expect(await screen.findByLabelText('When an analysis finishes')).toBeChecked()
    expect(screen.getByLabelText('When a collaboration suggestion arrives or becomes a match')).toBeChecked()
    expect(screen.getByLabelText('Weekly summary')).not.toBeChecked()
    expect(screen.getByText(/Mails never include names, papers or contact details/)).toBeInTheDocument()
  })

  it('sends a partial PATCH when toggled', async () => {
    const patches = stub((b) => new Response(JSON.stringify({ ...INITIAL, ...b })))
    renderIt()
    await userEvent.click(await screen.findByLabelText('Weekly summary'))
    await waitFor(() => expect(patches).toEqual([{ email_digest: true }]))
    expect(screen.getByLabelText('Weekly summary')).toBeChecked()
  })

  it('rolls back and shows an error when saving fails', async () => {
    stub(() => new Response('{}', { status: 500 }))
    renderIt()
    await userEvent.click(await screen.findByLabelText('Weekly summary'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save')
    expect(screen.getByLabelText('Weekly summary')).not.toBeChecked()
  })
})
