import { useConsent, type Me } from './api'

/** One labelled switch row (checkbox) with a hint, shared by consent and notification settings. */
export function Row({ id, title, hint, checked, disabled, dim, onChange }: { id: string; title: string; hint: string; checked: boolean; disabled: boolean; dim?: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className={`flex items-start justify-between gap-4 rounded-btn bg-subtle p-4 ${dim ? 'opacity-60' : ''}`}>
      <div>
        <label htmlFor={id} className="block text-sm font-bold">
          {title}
        </label>
        <p id={`${id}-hint`} className="text-sm text-ink-3">
          {hint}
        </p>
      </div>
      <input id={id} type="checkbox" aria-describedby={`${id}-hint`} className="mt-1 h-5 w-5 shrink-0 accent-teal" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
    </div>
  )
}

/** Matching and vault-signal consent switches (PATCH /me/consent). */
export function ConsentToggles({ me }: { me: Me }) {
  const consent = useConsent()
  return (
    <div className="space-y-3">
      <Row id="consent-matching" title="Suggest me to complementary researchers" hint="You stay anonymous. Contacts are shared only if you both accept." checked={me.consent_matching} disabled={consent.isPending} onChange={(v) => consent.mutate({ consent_matching: v })} />
      <Row id="consent-vault" title="Also use topic signals from my private vault" hint="Never the files themselves. After you both accept, joint ideas shown to both of you may draw on short statements from your documents (methods and aims). Requires matching." checked={me.consent_vault_signals} disabled={consent.isPending || !me.consent_matching} dim={!me.consent_matching} onChange={(v) => consent.mutate({ consent_vault_signals: v })} />
      {consent.isError && (
        <p role="alert" className="text-sm text-warm">
          Could not save your choice. Try again.
        </p>
      )}
    </div>
  )
}
