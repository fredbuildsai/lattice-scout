import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
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

interface Server { system?: unknown; docs: unknown[]; calls: { url: string; method: string; body?: unknown }[] }
function stub(server: Server) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    server.calls.push({ url, method, body: init?.body })
    const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status })
    if (method === 'GET' && url.endsWith('/api/v1/system/status')) return json(server.system ?? { accepting_runs: true, message: null })
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
      <MemoryRouter>
        <VaultPage upload={upload} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('VaultPage', () => {
  it('shows a document waiting for an approved provider with a clear status, and the system notice', async () => {
    const notice = 'No approved provider is configured, so Scout works from your published work only.'
    stub({ docs: [doc({ status: 'awaiting_approved_provider' })], calls: [], system: { accepting_runs: true, message: null, confidential_data_enabled: false, confidential_data_notice: notice } })
    renderPage()
    expect(await screen.findByText('Waiting for an approved provider')).toBeInTheDocument()
    expect(screen.getByText(/analysed automatically once the operator approves a provider/)).toBeInTheDocument()
    expect(await screen.findByText(notice)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull() // waiting is not a failure
    // a discard document that waits has lost its file but keeps its text until analysis: the row must not say "insights only"
    expect(screen.getByText(/file removed, text kept until analysed/)).toBeInTheDocument()
    expect(screen.queryByText(/insights only/)).toBeNull()
  })

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

  describe('Analyse with Scout', () => {
    const many = (n: number) => Array.from({ length: n }, (_, i) => doc({ id: `d${i + 1}`, filename: `doc-${i + 1}.pdf` }))
    const runStub = (server: Server, startStatus = 202) => {
      const base = stub(server)
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, init?: RequestInit) => {
          if (init?.method === 'POST' && url.endsWith('/api/v1/runs')) {
            server.calls.push({ url, method: 'POST', body: init.body })
            return startStatus === 202
              ? new Response(JSON.stringify({ id: 'run-1' }), { status: 202 })
              : new Response(JSON.stringify({ code: 'run_limit_reached' }), { status: startStatus })
          }
          return base(url, init)
        }),
      )
    }

    it('sends every ready document when nothing is ticked (up to 10) and opens the run page', async () => {
      const server: Server = { docs: many(3), calls: [] }
      runStub(server)
      renderPage()
      await userEvent.click(await screen.findByRole('button', { name: /Analyse with Scout/ }))
      await waitFor(() => expect(server.calls.some((c) => c.method === 'POST')).toBe(true))
      const post = server.calls.find((c) => c.method === 'POST')
      expect(JSON.parse(String(post?.body))).toEqual({ document_ids: ['d1', 'd2', 'd3'] })
    })

    it('with more than 10 ready documents, requires a choice and stops ticking at 10 (no 422)', async () => {
      const server: Server = { docs: many(11), calls: [] }
      runStub(server)
      renderPage()
      const button = await screen.findByRole('button', { name: /Analyse with Scout/ })
      expect(button).toBeDisabled()
      expect(screen.getByText(/Choose up to 10 documents/)).toBeInTheDocument()
      const user = userEvent.setup()
      for (let i = 1; i <= 10; i++) await user.click(screen.getByRole('checkbox', { name: `Select doc-${i}.pdf for analysis` }))
      expect(button).toBeEnabled()
      expect(screen.getByRole('checkbox', { name: 'Select doc-11.pdf for analysis' })).toBeDisabled()
      expect(screen.getByText(/up to 10 documents per analysis/)).toBeInTheDocument()
      await user.click(button)
      await waitFor(() => expect(server.calls.some((c) => c.method === 'POST')).toBe(true))
      const ids = JSON.parse(String(server.calls.find((c) => c.method === 'POST')?.body)).document_ids as string[]
      expect(ids).toHaveLength(10)
    })

    it('shows a plain message when the run cannot start', async () => {
      const server: Server = { docs: many(2), calls: [] }
      runStub(server, 429)
      renderPage()
      await userEvent.click(await screen.findByRole('button', { name: /Analyse with Scout/ }))
      expect(await screen.findByRole('alert')).toBeInTheDocument()
    })
  })
})
