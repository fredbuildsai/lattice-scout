import { useEffect, useId, useRef, useState } from 'react'
import { track } from '../../analytics/client'
import { doiUrl, openAlexUrl, pageLabel, useEvidenceOpen, type Evidence } from './api'

const LINK = 'inline-flex min-h-9 items-center rounded-btn border border-line-strong px-3 text-xs font-semibold text-accent hover:bg-accent-50'

function describe(e: Evidence): string {
  if (e.source_type === 'count') return `${e.count ?? '?'} papers for the query ${e.query ?? ''}`
  if (e.source_type === 'vault') return e.filename ?? 'your vault document'
  return e.title ?? 'a published paper'
}

/**
 * Numbered evidence marker. A button that opens a popover (a bottom sheet below 768 px) with the source behind a claim:
 * literature and profile works show title, authors, year, the supporting sentence and "Open DOI" / "Open in OpenAlex";
 * vault items show document, page and the highlighted quote and "Open passage" (loads `/evidence/{id}/open`);
 * count items show the query and the measured number. Esc or the Close button closes it.
 * Tracks `evidence_marker_opened` on open and `evidence_source_clicked` on every action.
 */
export function SourceMarker({ evidence, index, opportunityId, claimId }: { evidence: Evidence; index: number; opportunityId: string; claimId: string }) {
  const [open, setOpen] = useState(false)
  const [passage, setPassage] = useState(false)
  const root = useRef<HTMLSpanElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const titleId = useId()
  const e = evidence
  const detail = useEvidenceOpen(e.id, passage)

  useEffect(() => {
    if (!open) return
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        setOpen(false)
        button.current?.focus()
      }
    }
    const onDown = (ev: MouseEvent) => {
      if (root.current && !root.current.contains(ev.target as Node)) setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onDown)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onDown)
    }
  }, [open])

  const clicked = () => track('evidence_source_clicked', { opportunity_id: opportunityId, evidence_id: e.id, source_type: e.source_type })
  const toggle = () => {
    if (!open) track('evidence_marker_opened', { opportunity_id: opportunityId, claim_id: claimId })
    setOpen(!open)
  }
  const pages = pageLabel(e.page_from, e.page_to)
  const isWork = e.source_type === 'literature' || e.source_type === 'profile'
  const heading = e.source_type === 'count' ? 'Measured in OpenAlex' : e.source_type === 'vault' ? 'Your private vault' : e.source_type === 'profile' ? 'Your published work' : 'Literature'
  const d = detail.data
  const list = e.authors ?? []
  const authors = list.slice(0, 4).join(', ') + (list.length > 4 ? ' et al.' : '')

  return (
    <span ref={root} className="relative inline-block align-baseline">
      <button
        ref={button}
        type="button"
        aria-label={`Source ${index}: ${describe(e)}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={toggle}
        className="mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-scout-50 px-1 align-text-top font-mono text-[11px] font-semibold text-scout-ink hover:bg-scout hover:text-white"
      >
        {index}
      </button>
      {open && (
        <div
          role="dialog"
          aria-labelledby={titleId}
          className="fixed inset-x-0 bottom-0 z-50 max-h-[70vh] overflow-y-auto rounded-t-card border border-line bg-surface p-4 text-left text-sm font-normal text-ink shadow-lg md:absolute md:inset-x-auto md:bottom-auto md:left-0 md:top-full md:mt-1 md:w-[340px] md:rounded-card"
        >
          <p className="font-mono text-[11px] uppercase tracking-wide text-ink-3">{heading}</p>
          {isWork && (
            <>
              <p id={titleId} className="mt-1 font-semibold">
                {e.title ?? 'Untitled work'}
              </p>
              <p className="text-xs text-ink-3">{[authors, e.year].filter(Boolean).join(' · ')}</p>
              {e.quote && <blockquote className="mt-2 border-l-2 border-scout pl-3 font-serif italic text-ink-2">“{e.quote}”</blockquote>}
              <div className="mt-3 flex flex-wrap gap-2">
                {e.doi && (
                  <a className={LINK} href={doiUrl(e.doi)} target="_blank" rel="noopener noreferrer" onClick={clicked}>
                    Open DOI
                  </a>
                )}
                {e.work_id && (
                  <a className={LINK} href={openAlexUrl(e.work_id)} target="_blank" rel="noopener noreferrer" onClick={clicked}>
                    Open in OpenAlex
                  </a>
                )}
              </div>
            </>
          )}
          {e.source_type === 'vault' && (
            <>
              <p id={titleId} className="mt-1 font-semibold">
                {e.filename ?? 'Vault document'}
                {pages && <span className="font-normal text-ink-3"> · {pages}</span>}
              </p>
              {e.quote && (
                <blockquote className="mt-2 border-l-2 border-scout pl-3 font-serif text-ink-2">
                  <mark className="bg-scout-50 text-ink">“{e.quote}”</mark>
                </blockquote>
              )}
              {passage && detail.isPending && <p role="status" className="mt-2 text-ink-3">Loading the passage…</p>}
              {passage && detail.isError && <p role="alert" className="mt-2 text-warm">We could not load the passage.</p>}
              {passage && d && (
                <>
                  {d.passage && <p className="mt-2 whitespace-pre-wrap rounded-btn bg-subtle p-2 text-ink-2">{d.passage}</p>}
                  {!d.original_available && <p className="mt-2 text-xs text-ink-3">Original file not kept; showing the saved quote</p>}
                </>
              )}
              {!passage && (
                <div className="mt-3">
                  <button
                    type="button"
                    className={LINK}
                    onClick={() => {
                      clicked()
                      setPassage(true)
                    }}
                  >
                    Open passage
                  </button>
                </div>
              )}
            </>
          )}
          {e.source_type === 'count' && (
            <>
              <p id={titleId} className="mt-1 font-semibold">
                {e.count ?? '?'} papers{e.since_year ? ` since ${e.since_year}` : ''}
              </p>
              {e.query && <p className="mt-1 break-words font-mono text-xs text-ink-2">{e.query}</p>}
            </>
          )}
          <button type="button" onClick={() => { setOpen(false); button.current?.focus() }} className="mt-3 min-h-9 text-xs font-semibold text-ink-3 underline">
            Close
          </button>
        </div>
      )}
    </span>
  )
}
