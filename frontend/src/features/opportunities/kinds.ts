/** Opportunity kinds: label, badge classes and dot colour (design tokens only). */
export const KINDS: Record<string, { label: string; badge: string; dot: string }> = {
  resubmission: { label: 'Resubmission', badge: 'bg-warm-50 text-warm', dot: 'bg-warm-mid text-white' },
  white_spot: { label: 'White spot', badge: 'bg-scout-50 text-scout-ink', dot: 'bg-scout text-white' },
  synergy: { label: 'Synergy', badge: 'bg-teal-50 text-teal-dark', dot: 'bg-teal text-white' },
}

/** Fallback for kinds this client does not know. */
export function kindInfo(kind: string) {
  return KINDS[kind] ?? { label: kind.replace(/_/g, ' '), badge: 'bg-subtle text-ink-2', dot: 'bg-ink-3 text-white' }
}

/** Format a 0..1 score with two decimals. */
export const fmt = (n: number) => n.toFixed(2)
