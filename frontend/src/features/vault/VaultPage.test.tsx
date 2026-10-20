import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { VaultPage } from './VaultPage'
import { VaultError, type Uploader } from './api'

const doc = (over: Record<string, unknown> = {}) => ({
  id: 'd1', filename: 'proposal.pdf', mime: 'application/pdf', size_bytes: 2048, status: 'ready', kind: 'declined_grant', kind_source: 'scout',
  kind_suggested: 'declined_grant', retention: 'discard', original_available: false, pages: 12, created_at: '2026-10-20T00:00:00Z', updated_at: '2026-10-20T00:00:00Z', ...over,
})
const ref = { chunk_id: 'c1', page_from: 3, page_to: 4 }
const detail = {
  ...doc(),
  insights: {
    aims: [{ summary: 'Make lignin foams', refs: [ref] }], methods: [{ name: 'Casting', detail: null, refs: [ref] }], findings: [], open_questions: [], datasets: [], keywords: ['lignin'],
    reviewer_objections: [{ quote: 'Scale-up is unclear', summary: 'Scalability', refs: [ref] }], prompt_version: 'v1',
  },
}

interface Server { docs: unknown[]; calls: { url: string; method: string; body?: unknown }[] }
function stub(server: Server) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    server.calls.push({ url, method, body: init?.body })
    const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status })
    if (method === 'GET' && url.endsWith('/api/v1/documents')) return json({ items: server.docs })
    if (method === 'GET') return json(detail)
    if (method === 'PATCH') return json(doc({ kind: 'proposal', kind_source: 'user' }))
    if (method === 'DELETE') {
      server.docs = []
      return new Response(null, { status: 204 })
    }
    return json({}, 404)
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

function renderPage(upload?: Uploader) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <VaultPage upload={upload} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('VaultPage', () => {
  it('keeps the original-file checkbox unticked by default and explains it', async () => {
    stub({ docs: [], calls: [] })
    renderPage()
    expect(screen.getByLabelText(/Keep the original file/)).not.toBeChecked()
    expect(screen.getByText(/only the extracted insights and short cited quotes are kept/)).toBeInTheDocument()
    expect(await screen.findByText(/Nothing here yet/)).toBeInTheDocument()
  })

  it('shows ready documents with the extracted summary and details with quotes and pages', async () => {
    stub({ docs: [doc()], calls: [] })
    renderPage()
    expect(await screen.findByText('Ready')).toBeInTheDocument()
    expect(await screen.findByText(/1 aim · 1 method · 1 reviewer objection$/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show details' }))
    expect(screen.getByText(/Scale-up is unclear/)).toBeInTheDocument()
    expect(screen.getAllByText(/pp\. 3–4/).length).toBeGreaterThan(0)
  })

  it('shows friendly failure messages', async () => {
    stub({ docs: [doc({ id: 'd2', status: 'failed', failure_code: 'encrypted' }), doc({ id: 'd3', status: 'rejected', failure_code: 'zip_bomb' })], calls: [] })
    renderPage()
    expect(await screen.findByText('Couldn’t read')).toBeInTheDocument()
    expect(screen.getByText('Not supported')).toBeInTheDocument()
    expect(screen.getByText(/password-protected/)).toBeInTheDocument()
  })

  it('changes the kind with PATCH', async () => {
    const server: Server = { docs: [doc()], calls: [] }
    stub(server)
    renderPage()
    await userEvent.selectOptions(await screen.findByLabelText('Scout thinks this is…'), 'proposal')
    await waitFor(() => expect(server.calls.find((c) => c.method === 'PATCH')?.body).toBe(JSON.stringify({ kind: 'proposal' })))
  })

  it('asks for confirmation before deleting and explains what is removed', async () => {
    const server: Server = { docs: [doc()], calls: [] }
    stub(server)
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Delete proposal.pdf' }))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent(/removes the extracted text, the insights and the quotes/)
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(server.calls.some((c) => c.method === 'DELETE')).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Delete proposal.pdf' }))
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(server.calls.some((c) => c.method === 'DELETE')).toBe(true))
    expect(await screen.findByText(/Nothing here yet/)).toBeInTheDocument()
  })

  it('closes on Escape, returns focus to the opening Delete button and traps Tab', async () => {
    stub({ docs: [doc()], calls: [] })
    renderPage()
    const opener = await screen.findByRole('button', { name: 'Delete proposal.pdf' })
    await userEvent.click(opener)
    const dialog = screen.getByRole('dialog')
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' })
    const del = within(dialog).getByRole('button', { name: 'Delete' })
    expect(cancel).toHaveFocus()
    await userEvent.tab()
    expect(del).toHaveFocus()
    await userEvent.tab()
    expect(cancel).toHaveFocus()
    await userEvent.tab({ shift: true })
    expect(del).toHaveFocus()
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  })
})

describe('upload flow', () => {
  it('aborts in-flight uploads when the page unmounts', async () => {
    stub({ docs: [], calls: [] })
    let signal: AbortSignal | undefined
    const upload = vi.fn<Uploader>((_f, _k, _p, s) => {
      signal = s
      return new Promise(() => undefined)
    })
    const { unmount } = renderPage(upload)
    await userEvent.upload(screen.getByLabelText('Choose files', { selector: 'input' }), new File(['%PDF'], 'new.pdf', { type: 'application/pdf' }))
    expect(signal?.aborted).toBe(false)
    unmount()
    expect(signal?.aborted).toBe(true)
  })

  it('shows an error instead of hanging when the upload rejects with a bad response', async () => {
    stub({ docs: [], calls: [] })
    renderPage(async () => {
      throw new VaultError('http_error', 201)
    })
    await userEvent.upload(screen.getByLabelText('Choose files', { selector: 'input' }), new File(['%PDF'], 'new.pdf', { type: 'application/pdf' }))
    expect(await screen.findByText(/upload failed/)).toBeInTheDocument()
  })

  it('says the file is empty for file_rejected / empty_file', async () => {
    stub({ docs: [], calls: [] })
    renderPage(async () => {
      throw new VaultError('file_rejected', 422, 'empty_file')
    })
    await userEvent.upload(screen.getByLabelText('Choose files', { selector: 'input' }), new File(['%PDF'], 'new.pdf', { type: 'application/pdf' }))
    expect(await screen.findByText(/This file is empty/)).toBeInTheDocument()
  })

  const pdf = () => new File(['%PDF'], 'new.pdf', { type: 'application/pdf' })
  const choose = (f: File) => userEvent.upload(screen.getByLabelText('Choose files', { selector: 'input' }), f)

  it('uploads with progress, passing keep_original, then lists the document', async () => {
    const server: Server = { docs: [], calls: [] }
    stub(server)
    let release: () => void = () => undefined
    const upload = vi.fn<Uploader>((_f, _k, onProgress) => {
      onProgress(0.5)
      return new Promise((res) => {
        release = () => {
          server.docs = [doc({ id: 'n1', filename: 'new.pdf', status: 'uploaded', kind: null, kind_suggested: null })]
          res({ document: doc({ id: 'n1' }) as never, duplicate: false })
        }
      })
    })
    renderPage(upload)
    await userEvent.click(screen.getByLabelText(/Keep the original file/))
    await choose(pdf())
    expect(upload.mock.calls[0]![1]).toBe(true)
    expect(await screen.findByText('Uploading 50%')).toBeInTheDocument()
    act(() => release())
    expect(await screen.findByText('Uploaded', { selector: 'span.rounded-btn' })).toBeInTheDocument()
  })

  it('says "Already in your vault" for a duplicate (200)', async () => {
    stub({ docs: [], calls: [] })
    renderPage(async () => ({ document: doc() as never, duplicate: true }))
    await choose(pdf())
    expect(await screen.findByText('Already in your vault')).toBeInTheDocument()
  })

  it('shows the friendly message for API error codes', async () => {
    stub({ docs: [], calls: [] })
    renderPage(async () => {
      throw new VaultError('vault_limit_reached', 409)
    })
    await choose(pdf())
    expect(await screen.findByText(/vault is full/)).toBeInTheDocument()
  })

  it('rejects wrong types and big files before uploading', async () => {
    stub({ docs: [], calls: [] })
    const upload = vi.fn<Uploader>()
    renderPage(upload)
    await userEvent.upload(screen.getByLabelText('Choose files', { selector: 'input' }), new File(['x'], 'sheet.xlsx'), { applyAccept: false })
    expect(await screen.findByText(/Only PDF and Word/)).toBeInTheDocument()
    const big = new File(['x'], 'big.pdf')
    Object.defineProperty(big, 'size', { value: 26 * 1024 * 1024 })
    await choose(big)
    expect(await screen.findByText(/larger than 25 MB/)).toBeInTheDocument()
    expect(upload).not.toHaveBeenCalled()
  })
})

describe('polling', () => {
  it('refetches every 2 s while a document is processing, and stops once all are settled', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const server: Server = { docs: [doc({ status: 'extracting' })], calls: [] }
    stub(server)
    renderPage()
    const lists = () => server.calls.filter((c) => c.method === 'GET' && c.url.endsWith('/api/v1/documents')).length
    await screen.findByText('Reading')
    const n0 = lists()
    await act(() => vi.advanceTimersByTimeAsync(2100))
    expect(lists()).toBeGreaterThan(n0)
    server.docs = [doc({ status: 'ready' })]
    await act(() => vi.advanceTimersByTimeAsync(2100))
    await screen.findByText('Ready')
    const n1 = lists()
    await act(() => vi.advanceTimersByTimeAsync(6500))
    expect(lists()).toBe(n1)
  })
})
