import { ConsentToggles } from './ConsentToggles'
import { DataDeletion } from './DataDeletion'
import { useMe } from './api'

/** Minimal /settings/privacy (W10, M1 stage): consent switches, data deletion. */
export function PrivacyPage() {
  const me = useMe()
  return (
    <section aria-labelledby="page-title" className="max-w-[640px]">
      <h1 id="page-title" className="font-serif text-3xl font-semibold">
        Profile and privacy
      </h1>
      {me.isPending && (
        <p role="status" className="mt-4 text-ink-3">
          Loading…
        </p>
      )}
      {me.isError && (
        <p role="alert" className="mt-4 text-warm">
          We could not load your settings.
        </p>
      )}
      {me.data && (
        <div className="mt-6 space-y-4">
          <p className="text-ink-3">Signed in as {me.data.email}</p>
          <h2 className="text-lg font-semibold">Matching with other researchers</h2>
          <ConsentToggles me={me.data} />
          <DataDeletion email={me.data.email} />
        </div>
      )}
    </section>
  )
}
