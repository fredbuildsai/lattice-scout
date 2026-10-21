import { useMemo, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { BasisNote } from '../runs/BasisNote'
import { Icon } from '../../ui/icons'
import { Claim } from './Claim'
import { FeedbackPanel } from './FeedbackPanel'
import { NextSteps } from './NextSteps'
import { KindBadge } from './OpportunityCard'
import { doiUrl, openAlexUrl, pageLabel, useEvidenceOpen, useOpportunity, useSetStatus, type ClaimData, type Evidence, type OpportunityDetail } from './api'
import { fmt } from './kinds'
import { track } from '../../analytics/client'

const TABS = [
  { id: 'vault', label: 'Your private vault' },
  { id: 'profile', label: 'Your published work' },
  { id: 'literature', label: 'Literature' },
] as const

/** Unique evidence items across all claims, grouped by source type (count items are shown under "How crowded is it?"). */
export function groupEvidence(o: Pick<OpportunityDetail, 'rationale' | 'objection' | 'score_claims'>): Record<string, Evidence[]> {
  const seen = new Set<string>()
  const out: Record<string, Evidence[]> = { vault: [], profile: [], literature: [] }
  for (const c of [...o.rationale, ...o.objection, ...o.score_claims])
    for (const m of c.markers) {
      if (seen.has(m.id) || !out[m.source_type]) continue
      seen.add(m.id)
      out[m.source_type]!.push(m)
    }
  return out
}

function EvidenceRow({ e, opportunityId }: { e: Evidence; opportunityId: string }) {
  const [passage, setPassage] = useState(false)
  const detail = useEvidenceOpen(e.id, passage)
  const clicked = () => track('evidence_source_clicked', { opportunity_id: opportunityId, evidence_id: e.id, source_type: e.source_type })
  const link = 'inline-flex min-h-9 items-center text-xs font-semibold text-accent hover:underline'
  const pages = pageLabel(e.page_from, e.page_to)
  return (
    <li className="py-3">
      <p className="font-semibold">
        {e.source_type === 'vault' ? (e.filename ?? 'Vault document') : (e.title ?? 'Untitled work')}
        {pages && <span className="font-normal text-ink-3"> · {pages}</span>}
        {e.year && <span className="font-normal text-ink-3"> · {e.year}</span>}
      </p>
      {e.quote && <p className="mt-1 text-sm text-ink-2">“{e.quote}”</p>}
      <div className="flex flex-wrap gap-4">
        {e.doi && (
          <a className={link} href={doiUrl(e.doi)} target="_blank" rel="noopener noreferrer" onClick={clicked}>
            Open DOI
          </a>
        )}
        {e.work_id && (
          <a className={link} href={openAlexUrl(e.work_id)} target="_blank" rel="noopener noreferrer" onClick={clicked}>
            Open in OpenAlex
          </a>
        )}
        {e.source_type === 'vault' && !passage && (
          <button type="button" className={link} onClick={() => { clicked(); setPassage(true) }}>
            Open passage
          </button>
        )}
      </div>
      {passage && detail.isPending && <p role="status" className="text-sm text-ink-3">Loading the passage…</p>}
      {passage && detail.isError && <p role="alert" className="text-sm text-warm">We could not load the passage.</p>}
      {passage && detail.data && (
        <div className="mt-1 text-sm">
          {detail.data.passage && <p className="whitespace-pre-wrap rounded-btn bg-subtle p-2 text-ink-2">{detail.data.passage}</p>}
          {!detail.data.original_available && <p className="mt-1 text-xs text-ink-3">Original file not kept; showing the saved quote</p>}
        </div>
      )}
    </li>
  )
}

function EvidenceTabs({ o }: { o: OpportunityDetail }) {
  const groups = useMemo(() => groupEvidence(o), [o])
  const first = TABS.find((t) => groups[t.id]!.length > 0)?.id ?? 'vault'
  const [tab, setTab] = useState<string>(first)
  const items = groups[tab] ?? []
  return (
    <Card title="Evidence" aside="Every item links to its source">
      <div role="tablist" aria-label="Evidence by source" className="flex flex-wrap gap-x-4 border-b border-line">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls="evidence-panel"
            type="button"
            onClick={() => setTab(t.id)}
            className={`min-h-11 border-b-2 px-1 text-sm ${tab === t.id ? 'border-accent font-semibold' : 'border-transparent text-ink-2'}`}
          >
            {t.label} · {groups[t.id]!.length}
          </button>
        ))}
      </div>
      <div role="tabpanel" id="evidence-panel" aria-labelledby={`tab-${tab}`}>
        {items.length === 0 ? <p className="py-3 text-sm text-ink-3">Nothing cited from this source.</p> : <ul className="divide-y divide-line">{items.map((e) => <EvidenceRow key={e.id} e={e} opportunityId={o.id} />)}</ul>}
      </div>
    </Card>
  )
}

function Card({ title, aside, children }: { title: string; aside?: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="rounded-card border border-line bg-surface p-4 md:p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">{title}</h2>
        {aside && <p className="text-xs text-ink-3">{aside}</p>}
      </div>
      <div className="mt-3">{children}</div>
    </section>
  )
}

function ScoreBar({ label, value, shown, note }: { label: string; value: number; shown: string; note?: string }) {
  const pct = Math.round(Math.min(1, Math.max(0, value)) * 100)
  return (
    <div className="py-2">
      <div className="flex items-baseline justify-between text-sm">
        <span>{label}</span>
        <b className="font-mono">{shown}</b>
      </div>
      <div className="mt-1 h-1.5 rounded-full bg-scout-50" role="presentation">
        <div className="h-1.5 rounded-full bg-scout" style={{ width: `${pct}%` }} />
      </div>
      {note && <p className="mt-1 text-xs text-ink-3">{note}</p>}
    </div>
  )
}

function Claims({ claims, id }: { claims: ClaimData[]; id: string }) {
  return (
    <div className="space-y-3">
      {claims.map((c) => (
        <div key={c.id} className="leading-relaxed">
          <Claim claim={c} opportunityId={id} />
        </div>
      ))}
    </div>
  )
}

/**
 * Opportunity detail (W6 / M6): title and badges, save / dismiss, rationale and objections as claims with source markers,
 * evidence tabs, the measured novelty count, scores with the readiness checklist, a client-side next-steps checklist and feedback.
 * Features that do not exist yet (drafting, co-applicant, sharing) are intentionally absent.
 */
export function OpportunityDetailPage() {
  const { id = '' } = useParams()
  const q = useOpportunity(id)
  const set = useSetStatus()
  const o = q.data
  return (
    <section aria-labelledby="page-title">
      <p className="text-xs text-ink-3">
        <Link to="/opportunities" className="underline">
          Opportunities
        </Link>
      </p>
      {q.isPending && <p role="status" className="mt-4 text-ink-3">Loading the opportunity…</p>}
      {q.isError && (
        <p role="alert" className="mt-4 text-warm">
          We could not load this opportunity. It may have been removed.{' '}
          <button type="button" className="underline" onClick={() => void q.refetch()}>
            Try again
          </button>
        </p>
      )}
      {o && (
        <>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <KindBadge kind={o.kind} />
            <span className="rounded-btn bg-teal-50 px-2 py-0.5 font-mono text-[11px] font-semibold uppercase tracking-wide text-teal-dark">Verified · {o.verified_sources} sources</span>
            {o.clusters.map((c) => (
              <span key={c} className="text-xs text-ink-3">{c}</span>
            ))}
          </div>
          <h1 id="page-title" className="mt-2 max-w-[820px] font-serif text-3xl font-semibold leading-tight md:text-4xl">
            {o.title}
          </h1>
          <BasisNote note={o.basis_note} />
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {o.status === 'dismissed' ? (
              <button type="button" disabled={set.isPending} onClick={() => set.mutate({ id: o.id, status: 'shown' })} className="min-h-11 rounded-btn border border-line-strong px-4 text-sm font-semibold">
                Restore
              </button>
            ) : (
              <>
                <button
                  type="button"
                  aria-pressed={o.status === 'saved'}
                  disabled={set.isPending}
                  onClick={() => set.mutate({ id: o.id, status: o.status === 'saved' ? 'shown' : 'saved' })}
                  className="min-h-11 rounded-btn bg-accent px-4 text-sm font-semibold text-white hover:bg-accent-dark aria-pressed:bg-teal"
                >
                  {o.status === 'saved' ? 'Saved' : 'Save'}
                </button>
                <button type="button" disabled={set.isPending} onClick={() => set.mutate({ id: o.id, status: 'dismissed' })} className="min-h-11 px-3 text-sm text-ink-2 hover:underline">
                  Dismiss
                </button>
              </>
            )}
            {set.isError && <span role="alert" className="text-sm text-warm">Could not update</span>}
          </div>

          <div className="mt-6 grid gap-5 lg:grid-cols-[1fr_320px]">
            <div className="min-w-0 space-y-5">
              <section aria-label="Why I think this works" className="rounded-card border border-scout-50 bg-scout-100 p-4 md:p-5">
                <h2 className="flex items-center gap-2 text-sm font-semibold text-scout-ink">
                  <Icon name="sparkle" size={16} /> Why I think this works
                </h2>
                <div className="mt-3 font-serif text-lg">
                  <Claims claims={o.rationale.length > 0 ? o.rationale : o.why ? [o.why] : []} id={o.id} />
                </div>
              </section>

              {o.objection.length > 0 && (
                <Card title="Objections" aside="What a sceptical reviewer would say, checked against your sources">
                  <Claims claims={o.objection} id={o.id} />
                </Card>
              )}

              <EvidenceTabs o={o} />

              <Card title="How crowded is it?" aside="Measured in OpenAlex">
                {o.novelty.count == null ? (
                  <p className="text-sm text-ink-3">No measured count for this idea.</p>
                ) : (
                  <>
                    <p className="text-lg">
                      <b className="font-mono text-2xl">{o.novelty.count}</b> papers{o.novelty.since_year ? ` since ${o.novelty.since_year}` : ''} match this idea
                      {o.novelty.band && <span className="ml-2 rounded-btn bg-subtle px-2 py-0.5 text-xs">{o.novelty.band}</span>}
                    </p>
                    {o.novelty.query && (
                      <p className="mt-2 text-xs text-ink-3">
                        Query: <code className="break-words font-mono">{o.novelty.query}</code>
                      </p>
                    )}
                  </>
                )}
              </Card>

              {o.next_steps.length > 0 && (
                <Card title="Next steps" aside="Tick as you go; saved in this browser">
                  <NextSteps opportunityId={o.id} steps={o.next_steps} />
                </Card>
              )}
            </div>

            <aside className="min-w-0 space-y-5">
              <section aria-label="Scout's assessment" className="rounded-card border border-line bg-surface p-4">
                <h2 className="text-sm font-semibold">Scout’s assessment</h2>
                {o.scores ? (
                  <div className="mt-2 divide-y divide-line">
                    <ScoreBar label="Fit with your work" value={o.scores.fit} shown={`${fmt(o.scores.fit)} / 1`} />
                    <ScoreBar label="Novelty" value={o.scores.novelty} shown={o.novelty.count != null ? `${o.novelty.count} papers` : fmt(o.scores.novelty)} note={`Score ${fmt(o.scores.novelty)} / 1`} />
                    <ScoreBar label="Readiness" value={o.scores.readiness} shown={`${Math.round(o.scores.readiness * 100)}%`} />
                    <ScoreBar label="New effort" value={o.scores.effort} shown={o.effort_estimate ?? fmt(o.scores.effort)} note={o.effort_estimate ? `Effort score ${fmt(o.scores.effort)} / 1` : undefined} />
                  </div>
                ) : (
                  <p className="mt-2 text-sm text-ink-3">Not scored.</p>
                )}
                {o.score_claims.length > 0 && (
                  <div className="mt-3 space-y-2 border-t border-line pt-3 text-xs text-ink-2">
                    {o.score_claims.map((c) => (
                      <div key={c.id}>
                        <Claim claim={c} opportunityId={o.id} />
                      </div>
                    ))}
                  </div>
                )}
                {o.readiness.length > 0 && (
                  <div className="mt-3 border-t border-line pt-3">
                    <h3 className="text-xs font-semibold">Readiness checklist</h3>
                    <ul className="mt-1 space-y-1 text-sm">
                      {o.readiness.map((r, i) => (
                        <li key={i} className="flex gap-2">
                          <span aria-hidden="true" className={r.present ? 'text-teal-dark' : 'text-warm-mid'}>{r.present ? '✓' : '○'}</span>
                          <span>
                            {r.item} <span className="sr-only">{r.present ? '(in your vault)' : '(missing)'}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </section>
              <FeedbackPanel opportunityId={o.id} />
            </aside>
          </div>
        </>
      )}
    </section>
  )
}
