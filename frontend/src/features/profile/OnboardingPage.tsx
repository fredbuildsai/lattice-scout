import { useState, type FormEvent } from 'react'
import { ConsentToggles } from './ConsentToggles'
import { TopicChips } from './TopicChips'
import { WorksStep } from './WorksStep'
import { storedClaim, useCandidates, useClaim, useMe, useProfile, type CandidateGroup } from './api'
import { buildClaim, initialSelection, toggleRecord } from './claimSelection'

/**
 * W1 / M1 claim flow at /onboarding: search, pick a candidate card, tick records, set the matching
 * consent and confirm. Once a researcher exists the page shows import progress, then the works
 * confirmation step (`/onboarding?step=works` is the link Home uses to come back to it). "Search again"
 * shows the claim form even when a researcher exists.
 */
export function OnboardingPage() {
  const me = useMe()
  const [searchAgain, setSearchAgain] = useState(false)
  if (me.data?.researcher && !searchAgain) return <ImportProgress onSearchAgain={() => setSearchAgain(true)} />
  return <ClaimForm onClaimed={() => setSearchAgain(false)} />
}

function ClaimForm({ onClaimed }: { onClaimed: () => void }) {
  const me = useMe()
  const claim = useClaim()
  const [name, setName] = useState('')
  const [affiliation, setAffiliation] = useState('')
  const [submitted, setSubmitted] = useState<{ name: string; affiliation: string } | null>(null)
  const [chosen, setChosen] = useState<string | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const candidates = useCandidates(submitted?.name ?? '', submitted?.affiliation ?? '', !!submitted)
  const groups = candidates.data?.groups ?? []
  const group = groups.find((g) => g.group_id === chosen)

  function search(e: FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setChosen(null)
    setSelected([])
    setSubmitted({ name: name.trim(), affiliation: affiliation.trim() })
  }
  function choose(g: CandidateGroup) {
    setChosen(g.group_id)
    setSelected(initialSelection(g))
  }

  return (
    <section aria-labelledby="page-title" className="mx-auto max-w-[900px]">
      <h1 id="page-title" className="font-serif text-3xl font-semibold md:text-4xl">
        Is this you?
      </h1>
      <p className="mt-2 max-w-[640px] text-ink-2">Scout builds a starting profile from public sources (OpenAlex). Confirm what&apos;s yours: it decides what Scout compares your private work against, and how you&apos;re matched.</p>

      <form onSubmit={search} role="search" className="mt-6 grid gap-3 rounded-card border border-line bg-surface p-4 md:grid-cols-[1fr_1fr_auto] md:items-end">
        <label className="block text-sm font-semibold">
          Your name
          <input value={name} onChange={(e) => setName(e.target.value)} required autoComplete="off" className="mt-1 h-11 w-full rounded-btn border border-line-strong px-3 font-normal" />
        </label>
        <label className="block text-sm font-semibold">
          Affiliation <span className="font-normal text-ink-3">(optional)</span>
          <input value={affiliation} onChange={(e) => setAffiliation(e.target.value)} autoComplete="off" className="mt-1 h-11 w-full rounded-btn border border-line-strong px-3 font-normal" />
        </label>
        <button type="submit" className="h-11 rounded-btn bg-accent px-5 font-semibold text-white hover:bg-accent-dark">
          Search
        </button>
      </form>

      {candidates.isFetching && (
        <p role="status" className="mt-6 text-ink-3">
          Searching OpenAlex…
        </p>
      )}
      {candidates.isError && (
        <p role="alert" className="mt-6 text-warm">
          The search failed. Try again in a moment.
        </p>
      )}
      {submitted && candidates.isSuccess && !candidates.isFetching && groups.length === 0 && (
        <p className="mt-6 text-ink-2">No profiles found for “{submitted.name}”. Try another spelling or add your affiliation.</p>
      )}

      {groups.length > 0 && !candidates.isFetching && (
        <ul className="mt-6 space-y-4" aria-label="Candidate profiles">
          {groups.map((g) => {
            const active = g.group_id === chosen
            return (
              <li key={g.group_id} className={`rounded-card border bg-surface p-4 md:p-5 ${active ? 'border-accent ring-2 ring-accent-50' : 'border-line'}`}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-semibold">{g.primary.display_name}</h2>
                    <p className="text-sm text-ink-3">{g.institutions.join(' · ') || 'No affiliation listed'}{g.country ? ` · ${g.country}` : ''}</p>
                    <p className="mt-1 text-sm text-ink-2">
                      {g.works_count} works{g.cited_by_count > 0 && <> · {g.cited_by_count.toLocaleString('en-US')} citations</>}
                      {g.includes_records > 1 && <> · includes {g.includes_records} records</>}
                    </p>
                    {g.orcid && <p className="font-mono text-xs text-ink-3">ORCID {g.orcid}</p>}
                  </div>
                  <button type="button" aria-pressed={active} onClick={() => choose(g)} className={`h-10 rounded-btn border px-4 text-sm font-semibold ${active ? 'border-accent bg-accent text-white' : 'border-line-strong hover:bg-subtle'}`}>
                    {active ? 'This is me' : `This is me (${g.primary.display_name})`}
                  </button>
                </div>
                <div className="mt-3">
                  <TopicChips topics={g.top_topics} />
                </div>
                {active && (
                  <fieldset className="mt-4 border-t border-line pt-3">
                    <legend className="text-sm font-semibold">Records to include</legend>
                    {[...g.records, ...g.possible].map((r) => {
                      const isPrimary = r.author_id === g.primary.author_id
                      const possible = g.possible.some((p) => p.author_id === r.author_id)
                      return (
                        <label key={r.author_id} className="flex items-start gap-3 py-2 text-sm">
                          <input type="checkbox" className="mt-0.5 h-5 w-5 accent-accent" checked={selected.includes(r.author_id)} disabled={isPrimary} onChange={() => setSelected((s) => toggleRecord(g, s, r.author_id))} />
                          <span>
                            {r.display_name} · {r.works_count} works
                            <span className="block text-ink-3">
                              {r.institutions.join(' · ') || 'no affiliation'}
                              {possible && ' · possibly a namesake, left unticked'}
                            </span>
                          </span>
                        </label>
                      )
                    })}
                  </fieldset>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {group && me.data && (
        <div className="mt-6 space-y-4 rounded-card border border-line bg-surface p-4 md:p-5">
          <h2 className="text-lg font-semibold">Matching with other researchers</h2>
          <ConsentToggles me={me.data} />
        </div>
      )}

      {group && (
        <div className="mt-6 flex flex-col items-end gap-2">
          {claim.isError && (
            <p role="alert" className="text-sm text-warm">
              Could not save your profile. Try again.
            </p>
          )}
          <button type="button" disabled={claim.isPending} onClick={() => claim.mutate(buildClaim(group, selected), { onSuccess: onClaimed })} className="h-12 w-full rounded-btn bg-accent px-6 font-semibold text-white hover:bg-accent-dark disabled:opacity-60 md:w-auto">
            {claim.isPending ? 'Saving…' : 'Confirm'}
          </button>
        </div>
      )}
    </section>
  )
}

function ImportProgress({ onSearchAgain }: { onSearchAgain: () => void }) {
  const profile = useProfile()
  const claim = useClaim()
  const status = profile.data?.import.status
  if (profile.data && status === 'done') return <WorksStep profile={profile.data} />

  return (
    <section aria-labelledby="page-title" className="mx-auto max-w-[640px]">
      <h1 id="page-title" className="font-serif text-3xl font-semibold">
        Building your profile
      </h1>
      {profile.isError && (
        <p role="alert" className="mt-4 text-warm">
          We could not load your profile.{' '}
          <button type="button" className="underline" onClick={() => void profile.refetch()}>
            Try again
          </button>
        </p>
      )}
      {status === 'failed' && profile.data && (
        <div role="alert" className="mt-4 text-warm">
          <p>The import failed. Retry it, or search for your profile again.</p>
          {claim.isError && <p className="mt-1 text-sm">Could not restart the import. Try again.</p>}
          <div className="mt-3 flex flex-wrap gap-3">
            <button type="button" disabled={claim.isPending} onClick={() => claim.mutate(storedClaim(profile.data))} className="h-11 rounded-btn bg-accent px-5 font-semibold text-white hover:bg-accent-dark disabled:opacity-60">
              {claim.isPending ? 'Retrying…' : 'Retry import'}
            </button>
            <button type="button" onClick={onSearchAgain} className="h-11 rounded-btn border border-line-strong px-5 font-semibold text-ink hover:bg-subtle">
              Search again
            </button>
          </div>
        </div>
      )}
      {!profile.isError && status !== 'failed' && (
        <p role="status" className="mt-4 text-ink-2">
          Importing your works from OpenAlex. This takes a few seconds ({status ?? 'loading'}).
        </p>
      )}
    </section>
  )
}
