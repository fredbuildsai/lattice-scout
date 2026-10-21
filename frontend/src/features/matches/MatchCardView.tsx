import { useEffect, useState } from 'react'
import { track } from '../../analytics/client'
import { useProfile } from '../profile/api'
import { RatingForm } from './RatingForm'
import {
  errorText,
  mailtoUrl,
  openAlexEntityUrl,
  orcidUrl,
  scorePercent,
  useDecision,
  type AnonymousMatch,
  type MatchCard,
  type RevealedMatch,
} from './api'

const REGIONS: Record<string, string> = {
  africa: 'Africa',
  asia: 'Asia',
  europe: 'Europe',
  north_america: 'North America',
  south_america: 'South America',
  oceania: 'Oceania',
  unknown: 'Region not shared',
}
const BANDS: Record<string, string> = {
  early: 'Early career',
  mid: 'Mid career',
  senior: 'Senior',
  unknown: 'Career stage not shared',
}
const CHIP = 'rounded-full bg-subtle px-2.5 py-1 text-xs text-ink-2'
const BTN = 'inline-flex min-h-11 items-center rounded-btn px-4 text-sm font-semibold disabled:opacity-50'

const STATUS_LABEL: Record<string, string> = {
  awaiting_you: 'Waiting on you',
  waiting_on_them: 'Waiting on them',
  mutual: 'Mutual match',
  declined_by_you: 'Declined',
  expired: 'Expired',
}

function Bring({ title, items }: { title: string; items: { text: string }[] }) {
  return (
    <div>
      <h4 className="font-mono text-[11px] uppercase tracking-wide text-scout-ink">{title}</h4>
      <ul className="mt-1 space-y-1 text-sm">
        {items.map((b, i) => (
          <li key={i}>{b.text}</li>
        ))}
      </ul>
    </div>
  )
}

function Score({ score }: { score: number }) {
  return (
    <div className="text-right">
      <p className="text-2xl font-bold text-scout-ink">{scorePercent(score)}%</p>
      <p className="text-xs text-ink-3">complementary</p>
    </div>
  )
}

function Body({ m }: { m: MatchCard }) {
  return (
    <>
      {m.shared_topics.length > 0 && (
        <ul aria-label="Shared topics" className="mt-3 flex flex-wrap gap-2">
          {m.shared_topics.map((t) => (
            <li key={t} className={CHIP}>
              {t}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <Bring title="You bring" items={m.you_bring} />
        <Bring title="They bring" items={m.they_bring} />
      </div>
      <p className="mt-4 rounded-btn bg-scout-100 p-3 text-sm">
        <b>Why Scout matched you:</b> {m.why}
      </p>
    </>
  )
}

function Decision({ m }: { m: AnonymousMatch }) {
  const decide = useDecision()
  const [confirming, setConfirming] = useState(false)
  const [never, setNever] = useState(false)
  const date = (s: string) =>
    new Date(s).toLocaleDateString('en-GB', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    })
  const err = decide.isError ? (
    <p role="alert" className="mt-2 text-sm text-warm">
      {errorText(decide.error)}
    </p>
  ) : null

  if (m.status === 'expired')
    return (
      <p className="mt-4 text-sm text-ink-2">
        This proposal expired on {date(m.expires_at)}. No contact details were shared.
      </p>
    )
  if (m.status === 'declined_by_you')
    return <p className="mt-4 text-sm text-ink-2">You passed on this proposal. No contact details were shared.</p>
  if (m.status === 'waiting_on_them')
    return (
      <div className="mt-4">
        <p role="status" className="text-sm text-ink-2">
          Waiting for the other researcher. You said yes; contacts are shared only if they say yes too. This proposal
          stays open until {date(m.expires_at)}.
        </p>
        <button
          type="button"
          className={`${BTN} mt-2 border border-line-strong bg-surface`}
          disabled={decide.isPending}
          onClick={() => decide.mutate({ id: m.id, action: 'withdraw' })}
        >
          Withdraw
        </button>
        {err}
      </div>
    )
  if (m.status !== 'awaiting_you') return null
  return (
    <div className="mt-4">
      {!confirming ? (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={`${BTN} bg-scout text-white hover:bg-scout-ink`}
            disabled={decide.isPending}
            onClick={() => decide.mutate({ id: m.id, action: 'accept' })}
          >
            Accept
          </button>
          <button
            type="button"
            className={`${BTN} border border-line-strong bg-surface`}
            disabled={decide.isPending}
            onClick={() => setConfirming(true)}
          >
            Decline
          </button>
        </div>
      ) : (
        <div role="group" aria-label="Confirm decline" className="rounded-btn border border-line bg-subtle p-3">
          <p className="text-sm">Decline this proposal? The other researcher is not told.</p>
          <label className="mt-2 flex min-h-11 items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="h-5 w-5 accent-teal"
              checked={never}
              onChange={(e) => setNever(e.target.checked)}
            />
            Never suggest this person again
          </label>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              className={`${BTN} bg-ink text-white`}
              disabled={decide.isPending}
              onClick={() =>
                decide.mutate({
                  id: m.id,
                  action: 'decline',
                  neverSimilar: never,
                })
              }
            >
              Confirm decline
            </button>
            <button
              type="button"
              className={`${BTN} border border-line-strong bg-surface`}
              onClick={() => setConfirming(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {err}
    </div>
  )
}

function Anonymous({ m }: { m: AnonymousMatch }) {
  useEffect(() => track('match_viewed', { match_id: m.id }), [m.id])
  return (
    <>
      <article
        aria-label={`Anonymous match ${scorePercent(m.score)}%`}
        className="rounded-card border border-line bg-surface p-4 md:p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="font-serif text-xl font-semibold">Anonymous match</h3>
            <p className="mt-1 text-sm text-ink-3">{m.field_label}</p>
          </div>
          <Score score={m.score} />
        </div>
        <ul aria-label="About this researcher" className="mt-3 flex flex-wrap gap-2">
          <li className={CHIP}>{m.field_label}</li>
          <li className={CHIP}>{REGIONS[m.region] ?? m.region}</li>
          <li className={CHIP}>{BANDS[m.career_band] ?? m.career_band}</li>
          <li className="rounded-full bg-scout-50 px-2.5 py-1 text-xs font-semibold text-scout-ink">
            {STATUS_LABEL[m.status]}
          </li>
        </ul>
        <Body m={m} />
        <Decision m={m} />
      </article>
    </>
  )
}

function Ideas({ m }: { m: RevealedMatch }) {
  if (m.ideas_status === 'pending' || m.ideas_status === 'none')
    return (
      <p role="status" className="mt-4 text-sm text-ink-2">
        Ideas are being prepared. Check back in a few minutes.
      </p>
    )
  if (m.ideas_status === 'failed')
    return (
      <p className="mt-4 text-sm text-ink-2">
        Scout could not write joint ideas this time. You already have each other&apos;s contacts, so you can still get
        in touch.
      </p>
    )
  return (
    <div className="mt-4">
      <h4 className="font-semibold">Joint opportunities</h4>
      <ul aria-label="Joint opportunities" className="mt-2 space-y-3">
        {m.joint_ideas.map((i, k) => (
          <li key={k} className="rounded-btn border border-line p-3">
            <p className="font-serif text-lg font-semibold">{i.title}</p>
            <p className="mt-1 text-sm">{i.question}</p>
            <div className="mt-2 grid gap-3 sm:grid-cols-2">
              <Bring title="Uses from you" items={i.uses_from_you} />
              <Bring title="Uses from them" items={i.uses_from_them} />
            </div>
            <p className="mt-2 text-sm">
              <b>First step:</b> {i.first_step}
            </p>
            <p className="text-sm">
              <b>Possible output:</b> {i.possible_output}
            </p>
          </li>
        ))}
      </ul>
    </div>
  )
}

function initialsOf(name: string) {
  return name
    .split(/\s+/)
    .map((w) => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()
}

function Revealed({ m }: { m: RevealedMatch }) {
  useEffect(() => {
    track('match_viewed', { match_id: m.id })
  }, [m.id])
  const profile = useProfile()
  const c = m.contact
  const mine = profile.data ? initialsOf(profile.data.display_name) : null
  return (
    <article
      aria-label={`Mutual match ${c.display_name}`}
      className="overflow-hidden rounded-card border-2 border-scout bg-surface"
    >
      <div className="bg-scout p-5 text-center text-white">
        <div aria-hidden="true" className="flex justify-center">
          <span className="flex h-14 w-14 items-center justify-center rounded-full border-2 border-white bg-accent text-lg font-bold">
            {mine ?? 'You'}
          </span>
          <span className="-ml-3 flex h-14 w-14 items-center justify-center rounded-full border-2 border-white bg-surface text-lg font-bold text-scout-ink">
            {initialsOf(c.display_name)}
          </span>
        </div>
        <h3 className="mt-3 font-serif text-2xl font-semibold">You both said yes</h3>
        <p className="text-sm">Contacts shared.</p>
      </div>
      <div className="p-4 md:p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h4 className="font-serif text-xl font-semibold">{c.display_name}</h4>
            <p className="text-sm text-ink-3">{c.institution ?? 'Institution not shared'}</p>
          </div>
          <Score score={m.score} />
        </div>
        <dl className="mt-3 grid gap-3 sm:grid-cols-3">
          <div className="rounded-btn bg-subtle p-3">
            <dt className="text-xs text-ink-3">Email</dt>
            <dd className="break-all text-sm font-semibold">
              <a className="text-accent underline" href={`mailto:${c.email}`}>
                {c.email}
              </a>
            </dd>
          </div>
          <div className="rounded-btn bg-subtle p-3 sm:col-span-2">
            <dt className="text-xs text-ink-3">Public profile</dt>
            <dd className="flex flex-wrap gap-x-4 text-sm font-semibold">
              {c.orcid && (
                <a className="text-accent underline" href={orcidUrl(c.orcid)} target="_blank" rel="noreferrer noopener">
                  ORCID {c.orcid}
                </a>
              )}
              {c.openalex_ids.map((id) => (
                <a
                  key={id}
                  className="text-accent underline"
                  href={openAlexEntityUrl(id)}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  OpenAlex {id.replace(/^https?:\/\/openalex\.org\//, '')}
                </a>
              ))}
              {!c.orcid && c.openalex_ids.length === 0 && (
                <span className="font-normal text-ink-3">No public profile linked</span>
              )}
            </dd>
          </div>
        </dl>
        <div className="mt-3 flex flex-wrap gap-2">
          <a
            className={`${BTN} bg-scout text-white hover:bg-scout-ink`}
            href={mailtoUrl(c.email, 'Lattice Scout: introduction')}
          >
            Write an intro
          </a>
          <a
            className={`${BTN} border border-line-strong bg-surface`}
            href={mailtoUrl(c.email, 'Lattice Scout: could we have a call?')}
          >
            Propose call
          </a>
        </div>
        <Body m={m} />
        <Ideas m={m} />
        <RatingForm matchId={m.id} initialStars={m.my_rating} />
      </div>
    </article>
  )
}

/** Compact row for the list pane: anonymous label or revealed name, score and status. */
export function MatchListRow({ match: m }: { match: MatchCard }) {
  const revealed = m.kind === 'revealed'
  return (
    <div className="flex items-center gap-3">
      {revealed ? (
        <span
          aria-hidden="true"
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-scout text-sm font-bold text-white"
        >
          {initialsOf(m.contact.display_name)}
        </span>
      ) : (
        <span
          aria-hidden="true"
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border-2 border-dashed border-scout text-scout-ink"
        >
          ?
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className="block font-semibold">{revealed ? m.contact.display_name : 'Anonymous match'}</span>
        <span className="block text-xs text-ink-3">
          {revealed
            ? (m.contact.institution ?? 'Institution not shared')
            : `${m.field_label} · ${REGIONS[m.region] ?? m.region}`}
        </span>
        <span className="mt-1 inline-block rounded bg-scout-50 px-2 py-0.5 text-xs font-semibold text-scout-ink">
          {revealed ? 'Mutual match' : STATUS_LABEL[m.status]}
        </span>
      </span>
      <span className="text-sm font-bold text-scout-ink">{scorePercent(m.score)}%</span>
    </div>
  )
}

/** Detail pane for one match: anonymous proposal (accept, decline with confirm, waiting, closed states) or revealed mutual match (hero, contact, joint ideas, rating). */
export function MatchCardView({ match }: { match: MatchCard }) {
  return match.kind === 'revealed' ? <Revealed m={match} /> : <Anonymous m={match} />
}
