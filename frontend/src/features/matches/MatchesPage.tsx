import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useConsent, useMe } from '../profile/api'
import { MatchCardView, MatchListRow } from './MatchCardView'
import { useMatches, type MatchCard } from './api'

const SECTIONS: { title: string; pick: (m: MatchCard) => boolean }[] = [
  {
    title: 'Waiting on you',
    pick: (m) => m.kind === 'anonymous' && m.status === 'awaiting_you',
  },
  {
    title: 'Mutual matches',
    pick: (m) => m.kind === 'revealed' || m.status === 'mutual',
  },
  {
    title: 'Waiting on them',
    pick: (m) => m.kind === 'anonymous' && m.status === 'waiting_on_them',
  },
  {
    title: 'Closed',
    pick: (m) => m.kind === 'anonymous' && (m.status === 'declined_by_you' || m.status === 'expired'),
  },
]

/** Opt-in panel shown while matching is off: explains the double opt-in rule, PATCHes consent, then reloads the list. */
export function OptInPanel({ onDone }: { onDone: () => void }) {
  const consent = useConsent()
  return (
    <div className="mt-6 rounded-card border border-line bg-surface p-5">
      <h2 className="text-lg font-semibold">Turn on matching</h2>
      <p className="mt-1 text-ink-2">
        Scout can suggest researchers whose work complements yours. You stay anonymous: they see your field, region and
        career stage, the topics you share and short descriptions of the methods you bring, but no name, institution,
        titles or contact. Names and contacts are shared only if both of you accept. You can switch matching off at any
        time in Profile and privacy.
      </p>
      <button
        type="button"
        disabled={consent.isPending}
        onClick={() => consent.mutate({ consent_matching: true }, { onSuccess: onDone })}
        className="mt-3 inline-flex min-h-11 items-center rounded-btn bg-scout px-4 text-sm font-semibold text-white hover:bg-scout-ink disabled:opacity-50"
      >
        Suggest me to complementary researchers
      </button>
      {consent.isError && (
        <p role="alert" className="mt-2 text-sm text-warm">
          Could not save your choice. Try again.
        </p>
      )}
    </div>
  )
}

/**
 * Matches (W7 / M7 / M8): two panes. The list groups proposals by status and follows `next_cursor` ("Load more");
 * the detail pane shows the selected match (decision controls, or the "You both said yes" hero with contact,
 * joint ideas and a rating form). Below `lg` the list and detail alternate. When matching is off the page offers the opt-in.
 */
export function MatchesPage() {
  const matches = useMatches()
  const me = useMe()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const items = matches.data?.items ?? []
  const explicit = items.find((m) => m.id === selectedId) ?? null
  const shown = explicit ?? items[0] ?? null
  return (
    <section aria-labelledby="page-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 id="page-title" className="font-serif text-3xl font-semibold md:text-4xl">
            Matches
          </h1>
          <p className="mt-1 max-w-2xl text-ink-2">
            Researchers whose work complements yours. Both sides stay anonymous until both say yes; only then are names,
            contacts and joint opportunities shown.
          </p>
        </div>
        <Link
          to="/settings/privacy"
          className="inline-flex min-h-11 items-center rounded-btn border border-line-strong bg-surface px-4 text-sm font-semibold"
        >
          Matching preferences
        </Link>
      </div>
      {matches.isPending && (
        <p role="status" className="mt-6 text-ink-3">
          Loading matches…
        </p>
      )}
      {matches.isError && (
        <p role="alert" className="mt-6 text-warm">
          We could not load your matches.{' '}
          <button type="button" className="underline" onClick={() => void matches.refetch()}>
            Try again
          </button>
        </p>
      )}
      {matches.data && !matches.data.matching_enabled && (
        <OptInPanel
          onDone={() => {
            void matches.refetch()
            void me.refetch()
          }}
        />
      )}
      {matches.data?.matching_enabled && items.length === 0 && (
        <div className="mt-6 rounded-card border border-line bg-surface p-5">
          <p>Matching opens when more researchers in your field join — invite colleagues.</p>
        </div>
      )}
      {matches.data?.matching_enabled && shown && (
        <div className="mt-6 grid gap-6 lg:grid-cols-[340px_1fr]">
          <div className={explicit ? 'hidden lg:block' : ''}>
            {SECTIONS.map((s) => {
              const list = items.filter(s.pick)
              if (list.length === 0) return null
              return (
                <section key={s.title} aria-label={s.title} className="mb-5">
                  <h2 className="font-mono text-[11px] uppercase tracking-wide text-ink-3">
                    {s.title} · {list.length}
                  </h2>
                  <ul className="mt-2 space-y-3">
                    {list.map((m) => (
                      <li key={m.id} data-match-id={m.id}>
                        <button
                          type="button"
                          aria-current={m.id === shown.id ? 'true' : undefined}
                          onClick={() => setSelectedId(m.id)}
                          className={`block min-h-11 w-full rounded-card border bg-surface p-3 text-left ${m.id === shown.id ? 'border-2 border-scout' : 'border-line hover:bg-subtle'}`}
                        >
                          <MatchListRow match={m} />
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              )
            })}
            {matches.hasNextPage && (
              <button
                type="button"
                disabled={matches.isFetchingNextPage}
                onClick={() => void matches.fetchNextPage()}
                className="inline-flex min-h-11 items-center rounded-btn border border-line-strong bg-surface px-4 text-sm font-semibold disabled:opacity-50"
              >
                {matches.isFetchingNextPage ? 'Loading…' : 'Load more'}
              </button>
            )}
            {matches.isFetchNextPageError && (
              <p role="alert" className="mt-2 text-sm text-warm">
                Could not load more matches. Try again.
              </p>
            )}
          </div>
          <div className={explicit ? '' : 'hidden lg:block'}>
            <button
              type="button"
              onClick={() => setSelectedId(null)}
              className="mb-3 inline-flex min-h-11 items-center text-sm font-semibold text-accent lg:hidden"
            >
              ← All matches
            </button>
            <MatchCardView key={shown.id} match={shown} />
          </div>
        </div>
      )}
    </section>
  )
}
