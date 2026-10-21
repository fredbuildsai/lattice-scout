import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../../auth/AuthProvider'
import { fakeAuthClient } from '../../test-utils'
import { DataDeletion } from './DataDeletion'

const EMAIL = 'a@example.edu'

function stub(handlers: { vault?: Response; account?: Response }) {
  const calls: { url: string; body?: unknown }[] = []
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body })
    if (url.endsWith('/documents/delete-all')) return handlers.vault ?? new Response(JSON.stringify({ deleting: 3 }), { status: 202 })
    if (url.endsWith('/me/delete')) return handlers.account ?? new Response(JSON.stringify({ deleting: true }), { status: 202 })
    return new Response(JSON.stringify({ items: [] }), { status: 200 })
  })
  vi.stubGlobal('fetch', fn)
  return calls
}

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const client = fakeAuthClient()
  render(
    <QueryClientProvider client={qc}>
      <AuthProvider client={client}>
        <DataDeletion email={EMAIL} />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return { qc, client }
}

afterEach(() => vi.unstubAllGlobals())

describe('DataDeletion', () => {
  it('states that files are never used for training and what stays', async () => {
    stub({})
    renderIt()
    expect(await screen.findByText('Scout never trains models on your files.')).toBeInTheDocument()
    expect(screen.getByText(/Cost records without your identity/)).toBeInTheDocument()
  })

  it('enables Delete my vault only after typing DELETE, then reports the count', async () => {
    const calls = stub({})
    renderIt()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Delete my vault' }))
    const dialog = screen.getByRole('dialog')
    const confirm = dialog.querySelector('button[type=submit]') as HTMLButtonElement
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText(/Type DELETE/), 'delete')
    expect(confirm).toBeDisabled()
    await user.clear(screen.getByLabelText(/Type DELETE/))
    await user.type(screen.getByLabelText(/Type DELETE/), 'DELETE')
    expect(confirm).toBeEnabled()
    await user.click(confirm)
    expect(await screen.findByText('3 documents are being deleted.')).toBeInTheDocument()
    expect(calls.find((c) => c.url.endsWith('/documents/delete-all'))?.body).toBe(JSON.stringify({ confirm: 'DELETE' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('Escape closes the dialog without calling the API', async () => {
    const calls = stub({})
    renderIt()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Delete my vault' }))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(calls).toHaveLength(0)
  })

  it('gates account deletion on the retyped e-mail and signs out on 202', async () => {
    const calls = stub({})
    const { client } = renderIt()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Delete my account' }))
    const confirm = screen.getByRole('dialog').querySelector('button[type=submit]') as HTMLButtonElement
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText(/Retype your e-mail/), 'a@example.ed')
    expect(confirm).toBeDisabled()
    await user.type(screen.getByLabelText(/Retype your e-mail/), 'u')
    expect(confirm).toBeEnabled()
    await user.click(confirm)
    expect(await screen.findByText('Your account is being deleted')).toBeInTheDocument()
    expect(calls.find((c) => c.url.endsWith('/me/delete'))?.body).toBe(JSON.stringify({ confirm_email: EMAIL }))
    expect(client.getSession()).toBeNull()
  })

  it('shows the confirmation_mismatch error inline on 422', async () => {
    stub({ account: new Response(JSON.stringify({ code: 'confirmation_mismatch', detail: 'x' }), { status: 422 }) })
    renderIt()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Delete my account' }))
    await user.type(screen.getByLabelText(/Retype your e-mail/), EMAIL)
    await user.click(screen.getByRole('dialog').querySelector('button[type=submit]') as HTMLButtonElement)
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('does not match your account'))
    expect(screen.queryByText('Your account is being deleted')).not.toBeInTheDocument()
  })
})
