import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { latestDone, useRuns } from '../runs/api'
import { emptyReason } from '../runs/messages'
import { IdeasChart } from './IdeasChart'
import { OpportunityCard } from './OpportunityCard'
import { useOpportunities, type OpportunityCard as Card } from './api'
import { KINDS } from './kinds'

/** Filter cards by kind (`all` keeps everything); keeps server order (best fit first). */
export function filterByKind(cards: Card[], kind: string): Card[] {
  return kind === 'all' ? cards : cards.filter((c) => c.kind === kind)
}

/** Count cards per kind. */
export function countKinds(cards: Card[]): Record<string, number> {
  const out: Record<string, number> = {}
  for (const c of cards) out[c.kind] = (out[c.kind] ?? 0) + 1
  return out
}

/**
 * Opportunities (W5 / M5): kind chips with counts, status radio (New & saved / Dismissed), the "Where the ideas sit" chart
 * and the ranked cards for one run (`?run=` or the latest finished run). The server only supports one kind per request and
 * has no counts, so the page loads up to 50 ideas of the run and filters and counts them in the browser.
 */
export function OpportunitiesPage() {
  const [params] = useSearchParams()
  const runs = useRuns()
  const [kind, setKind] = useState('all')
  const [view, setView] = useState<'active' | 'dismissed'>('active')
  const wanted = params.get('run')
  const run = wanted ? (runs.data?.items.find((r) => r.id === wanted) ?? null) : latestDone(runs.data?.items)
  const runId = wanted ?? run?.id
  const opps = useOpportunities({ runId, status: view === 'dismissed' ? 'dismissed' : undefined, enabled: !!runId })
  const all = opps.data?.items ?? []
  const counts = countKinds(all)
  const cards = filterByKind(all, kind)
  const runNo = run ? `run of ${new Date(run.created_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}` : 'your latest run'

  return (
    <section aria-labelledby="page-title" className="grid gap-6 md:grid-cols-[220px_1fr]">
      <aside aria-label="Filters" className="space-y-5">
        <div>
          <h2 className="font-mono text-[11px] uppercase tracking-wide text-ink-3">Type</h2>
          <div role="group" aria-label="Type" className="mt-2 flex flex-wrap gap-2 md:flex-col md:gap-1">
            {[['all', 'All types'] as const, ...Object.entries(KINDS).map(([id, k]) => [id, k.label] as const)].map(([id, label]) => (
              <button
                key={id}
                type="button"
                aria-pressed={kind === id}
                onClick={() => setKind(id)}
                className="flex min-h-11 items-center justify-between gap-3 rounded-btn border border-line bg-surface px-3 text-sm aria-pressed:border-ink aria-pressed:font-semibold md:w-full"
              >
                <span>{label}</span>
                <span className="font-mono text-xs text-ink-3">{id === 'all' ? all.length : (counts[id] ?? 0)}</span>
              </button>
            ))}
          </div>
        </div>
        <fieldset>
          <legend className="font-mono text-[11px] uppercase tracking-wide text-ink-3">Status</legend>
          {([['active', 'New & saved'], ['dismissed', 'Dismissed']] as const).map(([id, label]) => (
            <label key={id} className="flex min-h-11 items-center gap-2 text-sm">
              <input type="radio" name="status" checked={view === id} onChange={() => setView(id)} className="h-4 w-4 accent-accent" />
              {label}
            </label>
          ))}
        </fieldset>
      </aside>

      <div className="min-w-0">
        <h1 id="page-title" className="font-serif text-3xl font-semibold md:text-4xl">
          Opportunities
        </h1>
        <p className="mt-1 text-ink-2">
          {runId ? `${all.length} ${view === 'dismissed' ? 'dismissed ' : ''}ideas from ${runNo} · every one cites the files and papers it builds on · best fit first` : 'Every idea cites the files and papers it builds on.'}
        </p>

        {(runs.isPending || (!!runId && opps.isPending)) && (
          <p role="status" className="mt-6 text-ink-3">
            Loading opportunities…
          </p>
        )}
        {(runs.isError || opps.isError) && (
          <p role="alert" className="mt-6 text-warm">
            We could not load your opportunities.{' '}
            <button type="button" className="underline" onClick={() => void (runs.isError ? runs.refetch() : opps.refetch())}>
              Try again
            </button>
          </p>
        )}
        {runs.data && !runId && (
          <div className="mt-6 rounded-card border border-line bg-surface p-5">
            <p>{emptyReason(runs.data.items[0])}</p>
            <Link to="/vault" className="mt-3 inline-flex min-h-11 items-center font-semibold text-accent hover:underline">
              Go to the vault
            </Link>
          </div>
        )}
        {opps.data && (
          <>
            {view === 'active' && all.length > 0 && (
              <div className="mt-5">
                <IdeasChart cards={cards} />
              </div>
            )}
            {all.length === 0 && (
              <div className="mt-6 rounded-card border border-line bg-surface p-5">
                <p>{view === 'dismissed' ? 'You have not dismissed any ideas.' : emptyReason(run)}</p>
              </div>
            )}
            {all.length > 0 && cards.length === 0 && <p className="mt-6 text-ink-2">No ideas of this type in this run.</p>}
            {cards.length > 0 && (
              <ul aria-label="Opportunities" className="mt-5 space-y-4">
                {cards.map((c, i) => (
                  <OpportunityCard key={c.id} card={c} position={i + 1} />
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </section>
  )
}
