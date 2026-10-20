import type { Topic } from './api'

/** Topic chips (names only), at most `limit`. */
export function TopicChips({ topics, limit = 8 }: { topics: Topic[]; limit?: number }) {
  const names = topics.map((t) => t.name).filter((n): n is string => !!n).slice(0, limit)
  if (names.length === 0) return null
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Topics">
      {names.map((n) => (
        <li key={n} className="rounded-full bg-accent-50 px-3 py-1 text-sm text-accent-dark">
          {n}
        </li>
      ))}
    </ul>
  )
}
