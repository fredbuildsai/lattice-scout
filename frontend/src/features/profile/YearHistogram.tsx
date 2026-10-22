import type { Profile } from './api'

const W = 320
const H = 96
const MAX_YEARS = 20

/** Included works per publication year, oldest first, gaps filled with 0, at most the latest `MAX_YEARS` years. */
export function yearCounts(works: Pick<Profile['works'][number], 'year' | 'included'>[]): [number, number][] {
  const years = works.filter((w) => w.included && typeof w.year === 'number').map((w) => w.year as number)
  if (years.length === 0) return []
  const last = Math.max(...years)
  const first = Math.max(Math.min(...years), last - MAX_YEARS + 1)
  const out: [number, number][] = []
  for (let y = first; y <= last; y++) out.push([y, years.filter((v) => v === y).length])
  return out
}

/**
 * Bar chart of the user's own included publications per year (from the profile; no extra request). An image with a
 * text summary for assistive tech plus the same numbers as a list; an empty state when no included work has a year.
 */
export function YearHistogram({ works }: { works: Pick<Profile['works'][number], 'year' | 'included'>[] }) {
  const data = yearCounts(works)
  if (data.length === 0)
    return (
      <p className="mt-3 text-sm text-ink-3" data-testid="year-histogram-empty">
        No publication years to chart yet.
      </p>
    )
  const firstYear = data[0]![0]
  const lastYear = data[data.length - 1]![0]
  const max = Math.max(...data.map(([, n]) => n))
  const slot = W / data.length
  const bar = Math.min(24, slot * 0.7)
  const total = data.reduce((s, [, n]) => s + n, 0)
  const summary = `Bar chart of ${total} included ${total === 1 ? 'publication' : 'publications'} per year, ${firstYear} to ${lastYear}.`
  return (
    <figure className="mt-3">
      <svg viewBox={`0 0 ${W} ${H + 16}`} role="img" aria-label={summary} className="h-auto w-full text-scout">
        {data.map(([y, n], i) => {
          const h = n === 0 ? 0 : Math.max(2, (n / max) * H)
          return (
            <rect key={y} x={i * slot + (slot - bar) / 2} y={H - h} width={bar} height={h} rx="2" fill="currentColor" />
          )
        })}
        <line x1="0" x2={W} y1={H} y2={H} stroke="currentColor" opacity="0.4" />
        <text x="0" y={H + 12} fontSize="10" fill="currentColor" className="text-ink-3">
          {firstYear}
        </text>
        <text x={W} y={H + 12} fontSize="10" fill="currentColor" textAnchor="end" className="text-ink-3">
          {lastYear}
        </text>
      </svg>
      <figcaption className="text-xs text-ink-3">Publications per year (included works)</figcaption>
      <ul className="sr-only" aria-label="Publications per year">
        {data
          .filter(([, n]) => n > 0)
          .map(([y, n]) => (
            <li key={y}>
              {y}: {n}
            </li>
          ))}
      </ul>
    </figure>
  )
}
