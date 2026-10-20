import { useEffect, useRef, useState } from 'react'
import { useDeleteDocument, useDocumentDetail, useSetKind, type Insights, type VaultDocument } from './api'
import { formatSize, failureMessage, KIND_LABELS, KINDS, statusChip, type ChipTone, type DocKind } from './status'

const TONES: Record<ChipTone, string> = {
  neutral: 'bg-subtle text-ink-2',
  busy: 'bg-scout-50 text-scout-ink',
  ok: 'bg-teal-50 text-teal-dark',
  bad: 'bg-warm-50 text-warm',
}

/** Status chip with plain-language text. */
export function StatusChip({ status }: { status: string }) {
  const { label, tone } = statusChip(status)
  return <span className={`inline-flex shrink-0 items-center rounded-btn px-2 py-0.5 text-xs font-semibold ${TONES[tone]}`}>{label}</span>
}

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`
}

/** One-line summary of what Scout extracted. */
export function insightsSummary(i: Insights): string {
  return [plural(i.aims.length, 'aim', 'aims'), plural(i.methods.length, 'method', 'methods'), plural(i.reviewer_objections.length, 'reviewer objection', 'reviewer objections')].join(' · ')
}

function pages(r: { page_from: number; page_to: number }) {
  return r.page_from === r.page_to ? `p. ${r.page_from}` : `pp. ${r.page_from}–${r.page_to}`
}

function Section({ title, items }: { title: string; items: { summary: string; refs: { chunk_id: string; page_from: number; page_to: number }[] }[] }) {
  if (items.length === 0) return null
  return (
    <div>
      <h4 className="text-sm font-semibold">{title}</h4>
      <ul className="mt-1 space-y-1 text-sm text-ink-2">
        {items.map((it, n) => (
          <li key={n}>
            {it.summary} {it.refs.length > 0 && <span className="font-mono text-xs text-ink-3">({it.refs.map(pages).join(', ')})</span>}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Expanded insights: aims, methods, findings, open questions and reviewer objections with quotes and page refs. */
export function InsightsDetail({ insights }: { insights: Insights }) {
  return (
    <div className="mt-3 space-y-3 border-t border-line pt-3">
      <Section title="Aims" items={insights.aims} />
      <Section title="Methods" items={insights.methods.map((m) => ({ summary: m.detail ? `${m.name}: ${m.detail}` : m.name, refs: m.refs }))} />
      <Section title="Findings" items={insights.findings} />
      <Section title="Open questions" items={insights.open_questions} />
      {insights.reviewer_objections.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold">Reviewer objections</h4>
          <ul className="mt-1 space-y-2">
            {insights.reviewer_objections.map((o, n) => (
              <li key={n} className="text-sm">
                <p className="text-ink-2">{o.summary}</p>
                <blockquote className="mt-1 border-l-2 border-line-strong pl-3 font-serif italic text-ink-2">
                  “{o.quote}” {o.refs.length > 0 && <span className="font-mono text-xs not-italic text-ink-3">({o.refs.map(pages).join(', ')})</span>}
                </blockquote>
              </li>
            ))}
          </ul>
        </div>
      )}
      {insights.keywords.length > 0 && <p className="text-xs text-ink-3">Keywords: {insights.keywords.join(', ')}</p>}
    </div>
  )
}

/** Confirm dialog that explains what deleting removes. Escape cancels; focus starts on Cancel. */
export function DeleteDialog({ doc, busy, error, onCancel, onConfirm }: { doc: VaultDocument; busy: boolean; error: boolean; onCancel: () => void; onConfirm: () => void }) {
  const cancel = useRef<HTMLButtonElement>(null)
  const onCancelRef = useRef(onCancel)
  onCancelRef.current = onCancel
  useEffect(() => {
    cancel.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onCancelRef.current()
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="del-title" aria-describedby="del-body" className="w-full max-w-[460px] rounded-card bg-surface p-5 shadow-lg">
        <h2 id="del-title" className="text-lg font-semibold">
          Delete “{doc.filename}”?
        </h2>
        <p id="del-body" className="mt-2 text-sm text-ink-2">
          This removes the extracted text, the insights and the quotes Scout took from it{doc.original_available ? ', and the stored original file' : ''}. It cannot be undone. Opportunities already found stay, but can no longer cite this document.
        </p>
        {error && (
          <p role="alert" className="mt-2 text-sm text-warm">
            Could not delete it. Try again.
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button ref={cancel} type="button" onClick={onCancel} className="min-h-11 rounded-btn border border-line-strong px-4 text-sm font-semibold">
            Cancel
          </button>
          <button type="button" disabled={busy} onClick={onConfirm} className="min-h-11 rounded-btn bg-warm px-4 text-sm font-semibold text-white disabled:opacity-60">
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

/** A vault document: name, status chip, extracted summary with expandable details, kind selector and delete. */
export function DocumentRow({ doc }: { doc: VaultDocument }) {
  const ready = doc.status === 'ready'
  const detail = useDocumentDetail(doc.id, ready)
  const setKind = useSetKind()
  const del = useDeleteDocument()
  const [open, setOpen] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const failed = doc.status === 'failed' || doc.status === 'rejected'
  const insights = detail.data?.insights
  const suggestion = doc.kind_suggested
  const current = doc.kind ?? 'other'
  return (
    <li className="rounded-card border border-line bg-surface p-4" aria-label={doc.filename}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="break-words font-semibold">{doc.filename}</p>
          <p className="text-xs text-ink-3">
            {doc.mime.endsWith('pdf') ? 'PDF' : 'DOCX'} · {formatSize(doc.size_bytes)}
            {doc.pages ? ` · ${doc.pages} pages` : ''} · {doc.original_available ? 'original kept' : 'insights only'}
          </p>
        </div>
        <StatusChip status={doc.status} />
      </div>

      {failed && (
        <p role="alert" className="mt-2 text-sm text-warm">
          {failureMessage(doc.failure_code)}
        </p>
      )}

      {ready && (
        <div className="mt-3">
          {detail.isPending && <p className="text-sm text-ink-3">Loading what Scout extracted…</p>}
          {detail.isError && <p className="text-sm text-warm">Could not load the extracted insights.</p>}
          {insights && (
            <>
              <p className="text-sm">
                <span className="font-semibold">What Scout extracted: </span>
                {insightsSummary(insights)}
              </p>
              <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} className="mt-1 min-h-11 text-sm font-semibold text-accent hover:underline">
                {open ? 'Hide details' : 'Show details'}
              </button>
              {open && <InsightsDetail insights={insights} />}
            </>
          )}
          {detail.data && !insights && <p className="text-sm text-ink-3">No insights were extracted from this document.</p>}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
        {(ready || doc.kind || suggestion) && (
          <div>
            <label htmlFor={`kind-${doc.id}`} className="block text-xs text-ink-3">
              Scout thinks this is…
            </label>
            <select
              id={`kind-${doc.id}`}
              value={current}
              disabled={setKind.isPending}
              onChange={(e) => setKind.mutate({ id: doc.id, kind: e.target.value as DocKind })}
              className="mt-1 min-h-11 rounded-btn border border-line-strong bg-surface px-3 text-sm"
            >
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {KIND_LABELS[k]}
                </option>
              ))}
            </select>
            <p className="mt-1 text-xs text-ink-3">
              {doc.kind_source === 'user' ? (
                <>You chose this{suggestion && suggestion !== doc.kind ? `; Scout suggested ${KIND_LABELS[suggestion]}` : ''}.</>
              ) : (
                'Scout’s suggestion. Change it if it is wrong.'
              )}
            </p>
            {setKind.isError && (
              <p role="alert" className="text-xs text-warm">
                Could not save that change.
              </p>
            )}
          </div>
        )}
        <button type="button" onClick={() => setConfirming(true)} aria-label={`Delete ${doc.filename}`} className="ml-auto min-h-11 rounded-btn border border-line-strong px-3 text-sm font-semibold text-warm hover:bg-warm-50">
          Delete
        </button>
      </div>

      {confirming && (
        <DeleteDialog
          doc={doc}
          busy={del.isPending}
          error={del.isError}
          onCancel={() => setConfirming(false)}
          onConfirm={() => del.mutate(doc.id, { onSuccess: () => setConfirming(false) })}
        />
      )}
    </li>
  )
}
