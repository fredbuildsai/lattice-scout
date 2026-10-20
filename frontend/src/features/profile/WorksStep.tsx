import { useState } from 'react'
import { Link } from 'react-router-dom'
import { TopicChips } from './TopicChips'
import { useToggleWork, type Profile, type Work } from './api'

const PAGE = 20
const REASONS: Record<string, string> = { non_article_type: 'Not an article', name_mismatch: 'Author name differs' }

/** Reason badge text for an uncertain work, or null. */
export function reasonLabel(reason?: string | null): string | null {
  return reason ? (REASONS[reason] ?? 'Uncertain') : null
}

function WorkRow({ work, onToggle }: { work: Work; onToggle: (w: Work, included: boolean) => void }) {
  const badge = reasonLabel(work.uncertain_reason)
  const meta = [work.type, work.year, work.cited_by > 0 ? `${work.cited_by.toLocaleString('en-US')} citations` : null].filter(Boolean)
  return (
    <li className="border-t border-line py-3 first:border-t-0">
      <label className="flex items-start gap-3">
        <input type="checkbox" className="mt-1 h-5 w-5 shrink-0 accent-accent" checked={work.included} onChange={(e) => onToggle(work, e.target.checked)} />
        <span className="min-w-0 flex-1">
          <span className="block font-serif text-base font-semibold">{work.title}</span>
          <span className="block text-sm text-ink-3">{meta.join(' · ')}</span>
        </span>
        {badge && <span className="shrink-0 rounded-btn bg-subtle px-2 py-0.5 text-xs font-semibold text-ink-2">{badge}</span>}
      </label>
    </li>
  )
}

/**
 * W1 / M1 "Publications & outputs" step: the works from GET /profile with a checkbox each
 * (PATCH /profile/works/{id}, optimistic with rollback), uncertain works unticked with a reason
 * badge, the topics computed from the included works and a Continue button.
 */
export function WorksStep({ profile }: { profile: Profile }) {
  const toggle = useToggleWork()
  const [all, setAll] = useState(false)
  const works = all ? profile.works : profile.works.slice(0, PAGE)
  const s = profile.works_summary
  return (
    <section aria-labelledby="page-title" className="mx-auto max-w-[900px]">
      <h1 id="page-title" className="font-serif text-3xl font-semibold md:text-4xl">
        Is this you, {profile.display_name.split(' ')[0]}?
      </h1>
      <p className="mt-2 max-w-[640px] text-ink-2">Untick anything that is not yours. Scout compares your private work against the included publications only.</p>

      <div className="mt-6 rounded-card border border-line bg-surface p-4 md:p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-lg font-semibold">Publications &amp; outputs</h2>
          <p role="status" aria-label="Works summary" className="text-sm text-ink-3">
            {s.included} of {s.total} included{s.uncertain > 0 && ` · ${s.uncertain} uncertain`}
          </p>
        </div>
        {toggle.isError && (
          <p role="alert" className="mt-2 text-sm text-warm">
            Could not save that change. It was reverted; try again.
          </p>
        )}
        {profile.works.length === 0 ? (
          <p className="mt-4 text-ink-2">No works were found on your records.</p>
        ) : (
          <ul aria-label="Works" className="mt-3">
            {works.map((w) => (
              <WorkRow key={w.id} work={w} onToggle={(x, included) => toggle.mutate({ id: x.id, included })} />
            ))}
          </ul>
        )}
        {!all && profile.works.length > PAGE && (
          <button type="button" onClick={() => setAll(true)} className="mt-2 min-h-11 text-sm font-semibold text-accent hover:underline">
            Show all {profile.works.length}
          </button>
        )}
      </div>

      {profile.topics.length > 0 && (
        <div className="mt-6 rounded-card border border-line bg-surface p-4 md:p-5">
          <h2 className="mb-3 text-lg font-semibold">Research topics Scout detected</h2>
          <TopicChips topics={profile.topics} limit={12} />
        </div>
      )}

      <div className="mt-6 flex justify-end">
        <Link to="/" className="inline-flex h-12 w-full items-center justify-center rounded-btn bg-accent px-6 font-semibold text-white hover:bg-accent-dark md:w-auto">
          Continue to Home
        </Link>
      </div>
    </section>
  )
}
