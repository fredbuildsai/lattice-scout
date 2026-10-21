import { useEffect, useState } from 'react'
import { FEEDBACK_REASONS, useFeedback, useGiveFeedback } from './api'
import { Thumbs } from './Thumbs'

/**
 * "Was this a good find?": reason chips (multi-select) and thumbs, wired to POST /feedback.
 * Prefilled from GET /feedback (404 means no rating yet). Toggling a chip after rating saves again with the same rating.
 */
export function FeedbackPanel({ opportunityId }: { opportunityId: string }) {
  const existing = useFeedback(opportunityId)
  const give = useGiveFeedback(opportunityId)
  const [reasons, setReasons] = useState<string[]>([])
  const [seeded, setSeeded] = useState(false)
  const fb = existing.data
  useEffect(() => {
    if (!seeded && existing.isSuccess) {
      setReasons(fb?.reasons ?? [])
      setSeeded(true)
    }
  }, [existing.isSuccess, fb, seeded])
  const rating = (give.data ?? fb)?.rating as 'up' | 'down' | undefined

  const toggle = (id: string) => {
    const next = reasons.includes(id) ? reasons.filter((r) => r !== id) : [...reasons, id]
    setReasons(next)
    if (rating) give.mutate({ rating, reasons: next })
  }
  return (
    <section aria-labelledby="fb-title" className="rounded-card border border-line bg-surface p-4">
      <h2 id="fb-title" className="text-sm font-semibold">
        Was this a good find?
      </h2>
      <div role="group" aria-label="Reasons" className="mt-3 flex flex-wrap gap-2">
        {FEEDBACK_REASONS.map((r) => (
          <button
            key={r.id}
            type="button"
            aria-pressed={reasons.includes(r.id)}
            onClick={() => toggle(r.id)}
            className="min-h-11 rounded-full border border-line-strong px-3 text-sm aria-pressed:border-scout aria-pressed:bg-scout-50 aria-pressed:text-scout-ink"
          >
            {r.label}
          </button>
        ))}
      </div>
      <div className="mt-3">
        <Thumbs opportunityId={opportunityId} rating={rating} reasons={reasons} />
      </div>
      <p role="status" className="mt-2 text-xs text-ink-3">
        {give.isSuccess ? 'Thanks, your feedback is saved.' : rating ? 'You rated this idea. You can change it.' : 'Your feedback tunes what Scout looks for next time.'}
      </p>
    </section>
  )
}
