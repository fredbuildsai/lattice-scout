import { Row } from './ConsentToggles'
import { useNotificationPrefs, useUpdateNotificationPrefs } from './api'

/** E-mail notification switches (GET/PATCH /me/notifications), optimistic with rollback on error. */
export function NotificationSettings() {
  const prefs = useNotificationPrefs()
  const update = useUpdateNotificationPrefs()
  return (
    <section aria-labelledby="notifications-title" className="space-y-3">
      <h2 id="notifications-title" className="text-lg font-semibold">
        E-mail notifications
      </h2>
      <p className="text-sm text-ink-3">Mails never include names, papers or contact details; open Scout to see them.</p>
      {prefs.isPending && (
        <p role="status" className="text-ink-3">
          Loading…
        </p>
      )}
      {prefs.isError && (
        <p role="alert" className="text-warm">
          We could not load your notification settings.
        </p>
      )}
      {prefs.data && (
        <>
          <Row id="notify-runs" title="When an analysis finishes" hint="One mail per finished analysis." checked={prefs.data.email_runs} disabled={false} onChange={(v) => update.mutate({ email_runs: v })} />
          <Row id="notify-matches" title="When a collaboration suggestion arrives or becomes a match" hint="Suggestions stay anonymous until you both accept." checked={prefs.data.email_matches} disabled={false} onChange={(v) => update.mutate({ email_matches: v })} />
          <Row id="notify-digest" title="Weekly summary" hint="Off by default." checked={prefs.data.email_digest} disabled={false} onChange={(v) => update.mutate({ email_digest: v })} />
        </>
      )}
      {update.isError && (
        <p role="alert" className="text-sm text-warm">
          Could not save your choice. Try again.
        </p>
      )}
    </section>
  )
}
