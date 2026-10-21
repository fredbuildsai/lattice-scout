import { useGiveFeedback } from './api'

const BTN = 'flex h-11 w-11 items-center justify-center rounded-btn border border-line bg-surface text-ink-2 hover:bg-subtle aria-pressed:border-scout aria-pressed:bg-scout-50 aria-pressed:text-scout-ink'

function ThumbIcon({ down }: { down?: boolean }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={down ? 'rotate-180' : ''}>
      <path d="M7 11v9H4v-9zM7 11l4-8c1.5 0 2.5 1 2.5 2.5V9H19a2 2 0 0 1 2 2.3l-1 6A2 2 0 0 1 18 19H7" />
    </svg>
  )
}

/** Presentational "Useful?" thumbs; `rating` marks the pressed one. */
export function ThumbButtons({ id, rating, pending, error, onRate }: { id: string; rating?: 'up' | 'down' | null; pending?: boolean; error?: boolean; onRate: (r: 'up' | 'down') => void }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-ink-3" id={`useful-${id}`}>
        Useful?
      </span>
      <div role="group" aria-labelledby={`useful-${id}`} className="flex gap-2">
        <button type="button" className={BTN} aria-pressed={rating === 'up'} aria-label="Useful: thumbs up" disabled={pending} onClick={() => onRate('up')}>
          <ThumbIcon />
        </button>
        <button type="button" className={BTN} aria-pressed={rating === 'down'} aria-label="Not useful: thumbs down" disabled={pending} onClick={() => onRate('down')}>
          <ThumbIcon down />
        </button>
      </div>
      {error && (
        <span role="alert" className="text-xs text-warm">
          Could not save
        </span>
      )}
    </div>
  )
}

/** Thumbs for a list card: posts /feedback without reasons and remembers the choice. */
export function Thumbs({ opportunityId, rating, onRated }: { opportunityId: string; rating?: 'up' | 'down' | null; onRated?: (r: 'up' | 'down') => void }) {
  const give = useGiveFeedback(opportunityId)
  return <ThumbButtons id={opportunityId} rating={rating} pending={give.isPending} error={give.isError} onRate={(r) => give.mutate({ rating: r, reasons: [] }, { onSuccess: () => onRated?.(r) })} />
}
