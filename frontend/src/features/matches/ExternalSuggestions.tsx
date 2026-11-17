import { useState } from 'react'
import {
  EXTERNAL_SCOPES,
  externalErrorText,
  openAlexEntityUrl,
  useExternalSuggestions,
  useRefreshExternal,
  useSetExternalState,
  type ExternalScope,
  type ExternalSuggestion,
} from './api'

const LAYER_LABEL: Record<ExternalScope, string> = {
  in_network: 'In your network',
  in_field: 'In your field',
}

/** One researcher who is not on Lattice Scout: public OpenAlex facts, a link to the profile, Save and Dismiss. */
export function ExternalCard({ s }: { s: ExternalSuggestion }) {
  const setState = useSetExternalState()
  const saved = s.state === 'saved'
  return (
    <li className="rounded-card border border-line bg-surface p-4" data-external-id={s.id}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold">
          <a href={openAlexEntityUrl(s.openalex_id)} target="_blank" rel="noreferrer" className="text-accent underline">
            {s.name}
          </a>
        </h3>
        <span className="font-mono text-[11px] uppercase tracking-wide text-ink-3">
          {LAYER_LABEL[s.layer]}
          {saved && ' · Saved'}
        </span>
      </div>
      {s.institution && <p className="text-sm text-ink-2">{s.institution}</p>}
      {s.layer === 'in_network' && s.vias.length > 0 && (
        <p className="mt-1 text-sm text-ink-2">via {s.vias.join(', ')}</p>
      )}
      {s.shared_topics.length > 0 && (
        <p className="mt-2 text-sm">
          <span className="text-ink-3">Shared topics: </span>
          {s.shared_topics.join(' · ')}
        </p>
      )}
      <p className="mt-2 text-sm text-ink-3">
        h-index {s.h_index} · {s.citations.toLocaleString('en')} citations · {s.recent_works} works in the last 3 years
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={setState.isPending}
          onClick={() => setState.mutate({ id: s.id, state: saved ? 'active' : 'saved' })}
          className="inline-flex min-h-11 items-center rounded-btn border border-line-strong bg-surface px-4 text-sm font-semibold disabled:opacity-50"
        >
          {saved ? 'Unsave' : 'Save'}
        </button>
        <button
          type="button"
          disabled={setState.isPending}
          onClick={() => setState.mutate({ id: s.id, state: 'dismissed' })}
          className="inline-flex min-h-11 items-center rounded-btn px-4 text-sm font-semibold text-ink-2 hover:bg-subtle disabled:opacity-50"
        >
          Dismiss
        </button>
      </div>
      {setState.isError && (
        <p role="alert" className="mt-2 text-sm text-warm">
          Could not update this suggestion. Try again.
        </p>
      )}
    </li>
  )
}

/**
 * "Researchers not on Lattice Scout yet": suggestions found in public OpenAlex data, so a researcher gets ideas even
 * while few colleagues are members. Two scope chips (in your network = co-authors of your co-authors; in your field =
 * outside your network, same topics), a "Find researchers" button (POST /matches/external/refresh) and cards with
 * Save and Dismiss. These people are never contacted by Lattice Scout and never enter the double opt-in.
 */
export function ExternalSuggestions() {
  const list = useExternalSuggestions()
  const refresh = useRefreshExternal()
  const [scopes, setScopes] = useState<ExternalScope[]>(EXTERNAL_SCOPES.map((x) => x.id))
  const items = list.data?.items ?? []
  const toggle = (id: ExternalScope) =>
    setScopes((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))
  return (
    <section aria-labelledby="external-title" className="mt-10 border-t border-line pt-6">
      <h2 id="external-title" className="font-serif text-2xl font-semibold">
        Researchers not on Lattice Scout yet
      </h2>
      <p className="mt-1 max-w-2xl text-ink-2">
        Found in public OpenAlex records, so you get ideas while few colleagues have joined. Lattice Scout never
        contacts them; reach out yourself if you like.
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2" role="group" aria-label="Where to look">
        {EXTERNAL_SCOPES.map((x) => (
          <button
            key={x.id}
            type="button"
            aria-pressed={scopes.includes(x.id)}
            onClick={() => toggle(x.id)}
            className="min-h-11 rounded-full border border-line-strong px-3 text-sm aria-pressed:border-scout aria-pressed:bg-scout-50 aria-pressed:text-scout-ink"
          >
            {x.label}
          </button>
        ))}
        <button
          type="button"
          disabled={scopes.length === 0 || refresh.isPending}
          onClick={() => refresh.mutate(EXTERNAL_SCOPES.map((x) => x.id).filter((id) => scopes.includes(id)))}
          className="inline-flex min-h-11 items-center rounded-btn bg-scout px-4 text-sm font-semibold text-white hover:bg-scout-ink disabled:opacity-50"
        >
          {refresh.isPending ? 'Searching…' : 'Find researchers'}
        </button>
      </div>
      {refresh.isError && (
        <p role="alert" className="mt-2 text-sm text-warm">
          {externalErrorText(refresh.error)}
        </p>
      )}
      {list.isPending && (
        <p role="status" className="mt-4 text-ink-3">
          Loading suggestions…
        </p>
      )}
      {list.isError && (
        <p role="alert" className="mt-4 text-warm">
          We could not load these suggestions.{' '}
          <button type="button" className="underline" onClick={() => void list.refetch()}>
            Try again
          </button>
        </p>
      )}
      {list.isSuccess && items.length === 0 && (
        <p className="mt-4 text-ink-3">
          {refresh.isSuccess
            ? 'No new researchers this time. Try the other scope, or search again later.'
            : 'No suggestions yet. Choose where to look and press “Find researchers”.'}
        </p>
      )}
      {items.length > 0 && (
        <ul className="mt-4 grid gap-3 md:grid-cols-2">
          {items.map((s) => (
            <ExternalCard key={s.id} s={s} />
          ))}
        </ul>
      )}
    </section>
  )
}
