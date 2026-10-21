import { useState } from 'react'
import { Link } from 'react-router-dom'
import { track } from '../../analytics/client'
import { Claim } from './Claim'
import { Thumbs } from './Thumbs'
import { useSetStatus, type OpportunityCard as Card } from './api'
import { fmt, kindInfo } from './kinds'

/** Kind badge (Resubmission / White spot / Synergy). */
export function KindBadge({ kind }: { kind: string }) {
  const k = kindInfo(kind)
  return <span className={`rounded-btn px-2 py-0.5 font-mono text-[11px] font-semibold uppercase tracking-wide ${k.badge}`}>{k.label}</span>
}

/**
 * Opportunity card (W5 / M5, and the home list when `compact`): number, kind badge, fit, title, why text with source markers,
 * Open / Save / Dismiss (Restore when dismissed) and thumbs.
 */
export function OpportunityCard({ card, position, compact = false }: { card: Card; position: number; compact?: boolean }) {
  const set = useSetStatus()
  const [rating, setRating] = useState<'up' | 'down' | null>(null)
  const k = kindInfo(card.kind)
  const saved = card.status === 'saved'
  const dismissed = card.status === 'dismissed'
  return (
    <li id={`opp-${card.id}`} className="rounded-card border border-line bg-surface p-4 md:p-5" aria-label={card.title}>
      <div className="flex items-center gap-2">
        {!compact && (
          <span aria-hidden="true" className={`flex h-6 w-6 items-center justify-center rounded-full font-mono text-xs font-semibold ${k.dot}`}>
            {position}
          </span>
        )}
        <KindBadge kind={card.kind} />
        {card.verified_sources > 0 && <span className="hidden text-xs text-ink-3 sm:inline">{card.verified_sources} verified sources</span>}
        {saved && <span className="text-xs font-semibold text-teal-dark">Saved</span>}
        {card.scores && (
          <span className="ml-auto text-xs text-ink-3">
            Fit <b className="font-mono text-sm text-ink">{fmt(card.scores.fit)}</b>
          </span>
        )}
      </div>
      <h3 className="mt-2 font-serif text-lg font-semibold leading-snug md:text-xl">{card.title}</h3>
      {card.why && <p className="mt-2 text-sm leading-relaxed text-ink-2"><Claim claim={card.why} opportunityId={card.id} /></p>}
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-line pt-3">
        <Link
          to={`/opportunities/${card.id}`}
          onClick={() => track('opportunity_opened', { opportunity_id: card.id, position })}
          className="inline-flex min-h-11 items-center rounded-btn bg-accent px-4 text-sm font-semibold text-white hover:bg-accent-dark"
        >
          Open
        </Link>
        {dismissed ? (
          <button type="button" disabled={set.isPending} onClick={() => set.mutate({ id: card.id, status: 'shown' })} className="min-h-11 rounded-btn border border-line-strong px-4 text-sm font-semibold">
            Restore
          </button>
        ) : (
          <>
            <button
              type="button"
              aria-pressed={saved}
              disabled={set.isPending}
              onClick={() => set.mutate({ id: card.id, status: saved ? 'shown' : 'saved' })}
              className="min-h-11 rounded-btn border border-line-strong px-4 text-sm font-semibold aria-pressed:bg-teal-50 aria-pressed:text-teal-dark"
            >
              {saved ? 'Saved' : 'Save'}
            </button>
            <button type="button" disabled={set.isPending} onClick={() => set.mutate({ id: card.id, status: 'dismissed' })} className="min-h-11 px-3 text-sm text-ink-2 hover:underline">
              Dismiss
            </button>
          </>
        )}
        {set.isError && (
          <span role="alert" className="text-xs text-warm">
            Could not update
          </span>
        )}
        {!compact && (
          <div className="ml-auto">
            <Thumbs opportunityId={card.id} rating={rating} onRated={setRating} />
          </div>
        )}
      </div>
    </li>
  )
}
