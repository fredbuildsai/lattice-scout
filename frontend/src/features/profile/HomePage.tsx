import { TopicChips } from './TopicChips'
import { useProfile } from './api'

/** Home (W2, M1 stage): a minimal profile summary until runs, ideas and matches exist. */
export function HomePage() {
  const profile = useProfile()
  const p = profile.data
  return (
    <section aria-labelledby="page-title">
      <h1 id="page-title" className="font-serif text-3xl font-semibold">
        Home
      </h1>
      {profile.isPending && (
        <p role="status" className="mt-4 text-ink-3">
          Loading your profile…
        </p>
      )}
      {profile.isError && (
        <p role="alert" className="mt-4 text-warm">
          We could not load your profile.{' '}
          <button type="button" className="underline" onClick={() => void profile.refetch()}>
            Try again
          </button>
        </p>
      )}
      {p && (
        <div role="region" className="mt-4 max-w-[640px] rounded-card border border-line bg-surface p-5" aria-label="Profile summary">
          {p.import.status !== 'done' && (
            <p role="status" className="mb-3 text-sm text-ink-3">
              {p.import.status === 'failed' ? 'The last import failed.' : 'Still importing your works…'}
            </p>
          )}
          <h2 className="text-xl font-semibold">{p.display_name}</h2>
          <p className="text-ink-3">{p.institution ?? 'No affiliation listed'}</p>
          <p className="mt-3 text-ink-2">
            <b>{p.works_summary.included}</b> of {p.works_summary.total} works included
          </p>
          <div className="mt-3">
            <TopicChips topics={p.topics} />
          </div>
        </div>
      )}
    </section>
  )
}
