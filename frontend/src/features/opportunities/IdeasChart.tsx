import type { OpportunityCard } from './api'
import { fmt, kindInfo } from './kinds'

const W = 600
const H = 240
const PAD = { l: 36, r: 12, t: 12, b: 12 }
const FILL: Record<string, string> = { resubmission: '#B4570F', white_spot: '#5B3FB5', synergy: '#0F6E6E' }

/**
 * "Where the ideas sit": SVG scatter of novelty (x) by readiness (y) using the real scores, one numbered dot per card
 * (numbers match the card order). Exposed to assistive tech as an image with a summary plus a text list.
 */
export function IdeasChart({ cards }: { cards: OpportunityCard[] }) {
  const pts = cards.map((c, i) => ({ c, n: i + 1 })).filter((p) => p.c.scores)
  const x = (v: number) => PAD.l + Math.min(1, Math.max(0, v)) * (W - PAD.l - PAD.r)
  const y = (v: number) => H - PAD.b - Math.min(1, Math.max(0, v)) * (H - PAD.t - PAD.b)
  const summary = `Scatter chart of ${pts.length} ideas by novelty and readiness. ${pts.map((p) => `${p.n}: novelty ${fmt(p.c.scores!.novelty)}, readiness ${fmt(p.c.scores!.readiness)}`).join('; ')}.`
  return (
    <section aria-labelledby="chart-title" className="rounded-card border border-line bg-surface p-4 md:p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="chart-title" className="text-sm font-semibold">
          Where the ideas sit
        </h2>
        <p className="text-xs text-ink-3">Top right: novel, and you already have most of what’s needed</p>
      </div>
      {pts.length === 0 ? (
        <p className="mt-3 text-sm text-ink-3">No scored ideas to plot yet.</p>
      ) : (
        <>
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary} className="mt-3 h-auto w-full">
            <rect x={PAD.l} y={PAD.t} width={W - PAD.l - PAD.r} height={H - PAD.t - PAD.b} fill="#F1F2EF" />
            <line x1={x(0.5)} x2={x(0.5)} y1={PAD.t} y2={H - PAD.b} stroke="#C4C8C1" strokeDasharray="4 4" />
            <line x1={PAD.l} x2={W - PAD.r} y1={y(0.5)} y2={y(0.5)} stroke="#C4C8C1" strokeDasharray="4 4" />
            <text x={W - PAD.r - 6} y={PAD.t + 14} textAnchor="end" fontSize="11" fontWeight="600" fill="#0F6E6E">
              Sweet spot
            </text>
            <text x={10} y={H / 2} fontSize="11" fill="#555C63" transform={`rotate(-90 10 ${H / 2})`} textAnchor="middle">
              Readiness →
            </text>
            {pts.map((p) => (
              <g key={p.c.id} transform={`translate(${x(p.c.scores!.novelty)} ${y(p.c.scores!.readiness)})`}>
                <circle r="11" fill={FILL[p.c.kind] ?? '#555C63'} stroke="#fff" strokeWidth="2" />
                <text textAnchor="middle" dy="4" fontSize="11" fontWeight="600" fill="#fff" fontFamily="IBM Plex Mono, monospace">
                  {p.n}
                </text>
              </g>
            ))}
          </svg>
          <p className="text-right text-xs text-ink-3">Novelty (fewer existing papers) →</p>
          <ul className="sr-only" aria-label="Chart values">
            {pts.map((p) => (
              <li key={p.c.id}>
                {p.n}. {kindInfo(p.c.kind).label}: novelty {fmt(p.c.scores!.novelty)}, readiness {fmt(p.c.scores!.readiness)}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
