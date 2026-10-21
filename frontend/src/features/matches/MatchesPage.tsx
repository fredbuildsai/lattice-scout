import { Link } from 'react-router-dom'
import { useConsent, useMe } from '../profile/api'
import { MatchCardView } from './MatchCardView'
import { useMatches, type MatchCard } from './api'

const SECTIONS: { title: string; pick: (m: MatchCard) => boolean }[] = [
  { title: 'Waiting on you', pick: (m) => m.kind === 'anonymous' && m.status === 'awaiting_you' },
  { title: 'Mutual matches', pick: (m) => m.kind === 'revealed' || m.status === 'mutual' },
  { title: 'Waiting on them', pick: (m) => m.kind === 'anonymous' && m.status === 'waiting_on_them' },
  { title: 'Closed', pick: (m) => m.kind === 'anonymous' && (m.status === 'declined_by_you' || m.status === 'expired') },
]

/** Opt-in panel shown while matching is off: explains the double opt-in rule, PATCHes consent, then reloads the list. */
export function OptInPanel({ onDone }: { onDone: () => void }) {
  const consent = useConsent()
  return (
    <div className="mt-6 rounded-card border border-line bg-surface p-5">
      <h2 className="text-lg font-semibold">Turn on matching</h2>
      <p className="mt-1 text-ink-2">Scout can suggest researchers whose work complements yours. You stay anonymous: they see only your field, region and career stage. Names and contacts are shared only if both of you accept. You can switch matching off at any time in Profile and privacy.</p>
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
 * Matches (W7 / M7 / M8): proposals grouped by status. Anonymous cards carry the decision controls, mutual
 * matches show contact, joint ideas and a rating form. When matching is off the page offers the opt-in.
 */
export function MatchesPage() {
  const matches = useMatches()
  const me = useMe()
  const items = matches.data?.items ?? []
  return (
    <section aria-labelledby="page-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 id="page-title" className="font-serif text-3xl font-semibold md:text-4xl">
            Matches
          </h1>
          <p className="mt-1 max-w-2xl text-ink-2">Researchers whose work complements yours. Both sides stay anonymous until both say yes; only then are names, contacts and joint opportunities shown.</p>
        </div>
        <Link to="/settings/privacy" className="inline-flex min-h-11 items-center rounded-btn border border-line-strong bg-surface px-4 text-sm font-semibold">
          Matching preferences
        </Link>
      </div>
      {matches.isPending && <p role="status" className="mt-6 text-ink-3">Loading matches…</p>}
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
      {matches.data?.matching_enabled &&
        SECTIONS.map((s) => {
          const list = items.filter(s.pick)
          if (list.length === 0) return null
          return (
            <section key={s.title} aria-label={s.title} className="mt-6">
              <h2 className="font-mono text-[11px] uppercase tracking-wide text-ink-3">
                {s.title} · {list.length}
              </h2>
              <ul className="mt-2 space-y-4">
                {list.map((m) => (
                  <MatchCardView key={m.id} match={m} />
                ))}
              </ul>
            </section>
          )
        })}
    </section>
  )
}
