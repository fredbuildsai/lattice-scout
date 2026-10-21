import { useEffect, useState } from 'react'
import { MATCH_REASONS, errorText, useMatchFeedback, useRateMatch, type MatchReason } from './api'

const LABELS = ['Poor', 'Fair', 'OK', 'Good', 'Excellent']

/**
 * "How good was this match?": 1-5 stars, reason chips and a private comment (POST /matches/{id}/rating).
 * Prefilled from GET /feedback (404 means not rated yet) and from `initialStars`.
 */
export function RatingForm({ matchId, initialStars }: { matchId: string; initialStars?: number | null }) {
  const existing = useMatchFeedback(matchId)
  const rate = useRateMatch(matchId)
  const [stars, setStars] = useState(initialStars ?? 0)
  const [reasons, setReasons] = useState<MatchReason[]>([])
  const [comment, setComment] = useState('')
  const [seeded, setSeeded] = useState(false)
  useEffect(() => {
    if (!seeded && existing.isSuccess) {
      const fb = existing.data
      if (fb) {
        setStars(fb.stars ?? initialStars ?? 0)
        setReasons(fb.reasons as MatchReason[])
        setComment(fb.comment ?? '')
      }
      setSeeded(true)
    }
  }, [seeded, existing.isSuccess, existing.data, initialStars])

  const toggle = (id: MatchReason) => setReasons((r) => (r.includes(id) ? r.filter((x) => x !== id) : r.length >= 5 ? r : [...r, id]))
  return (
    <section aria-label="Rate this match" className="mt-5 border-t border-line pt-4">
      <h4 className="font-semibold">How good was this match?</h4>
      <div role="group" aria-label="Stars" className="mt-3 flex items-center gap-2">
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            type="button"
            aria-label={`${n} ${n === 1 ? 'star' : 'stars'}`}
            aria-pressed={stars === n}
            onClick={() => setStars(n)}
            className="flex h-11 w-11 items-center justify-center rounded-btn border border-line bg-surface text-xl text-warm aria-pressed:border-warm"
          >
            <span aria-hidden="true">{n <= stars ? '★' : '☆'}</span>
          </button>
        ))}
        {stars > 0 && <span className="text-sm text-ink-3">{LABELS[stars - 1]}</span>}
      </div>
      <div role="group" aria-label="Reasons" className="mt-3 flex flex-wrap gap-2">
        {MATCH_REASONS.map((r) => (
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
      <label htmlFor={`cmt-${matchId}`} className="mt-3 block text-sm font-semibold">
        Anything else?
      </label>
      <p className="text-xs text-ink-3">Private: only Scout sees this</p>
      <textarea
        id={`cmt-${matchId}`}
        value={comment}
        maxLength={1000}
        onChange={(e) => setComment(e.target.value)}
        placeholder="e.g. Great methods fit, but different timelines"
        className="mt-1 min-h-20 w-full rounded-btn border border-line-strong bg-surface p-2 text-sm"
      />
      <div className="mt-3 flex items-center gap-3">
        <button
          type="button"
          disabled={stars === 0 || rate.isPending}
          onClick={() => rate.mutate({ stars, reasons, comment })}
          className="min-h-11 rounded-btn bg-ink px-4 text-sm font-semibold text-white disabled:opacity-50"
        >
          Send feedback
        </button>
        <p role="status" className="text-sm text-ink-3">
          {rate.isSuccess ? 'Thanks, your feedback is saved.' : ''}
        </p>
        {rate.isError && (
          <p role="alert" className="text-sm text-warm">
            {errorText(rate.error)}
          </p>
        )}
      </div>
    </section>
  )
}
