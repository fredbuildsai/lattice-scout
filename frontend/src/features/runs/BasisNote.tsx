/**
 * Honest note shown on a run or an opportunity whose analysis used published work only (ADR 016). The wording
 * comes from the API (`basis_note`); nothing renders for a full analysis.
 */
export function BasisNote({ note }: { note?: string | null }) {
  if (!note) return null
  return (
    <p role="note" className="mt-3 rounded-card border border-line bg-subtle p-3 text-sm text-ink-2">
      {note}
    </p>
  )
}
