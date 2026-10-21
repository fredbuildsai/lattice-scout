import { useParams } from 'react-router-dom'

/** Placeholder page with a title; real screens replace these in later milestones. */
export function EmptyPage({ title }: { title: string }) {
  const { id } = useParams()
  return (
    <section aria-labelledby="page-title">
      <h1 id="page-title" className="font-serif text-3xl font-semibold text-ink">
        {title}
      </h1>
      {id && <p className="mt-1 font-mono text-xs text-ink-3">{id}</p>}
      <p className="mt-3 text-ink-3">This page is not built yet.</p>
    </section>
  )
}
