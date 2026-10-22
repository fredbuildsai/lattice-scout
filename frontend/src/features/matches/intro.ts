const MAX_ITEMS = 3
const MAX_TEXT = 160

export type IntroInput = {
  /** Display name of the revealed contact (the only person named besides the sender). */
  contactName: string
  /** Display name of the signed-in user, for the sign-off. */
  myName?: string | null
  sharedTopics: string[]
  youBring: { text: string }[]
  theyBring: { text: string }[]
}

const clip = (s: string) => {
  const t = s.replace(/\s+/g, ' ').trim()
  return t.length > MAX_TEXT ? `${t.slice(0, MAX_TEXT - 1).trimEnd()}…` : t
}
const items = (xs: { text: string }[]) =>
  xs
    .map((x) => clip(x.text))
    .filter(Boolean)
    .slice(0, MAX_ITEMS)
    .map((t) => `- ${t}`)

/**
 * Deterministic intro draft (no LLM, nothing sent). Uses only the shared topics and the you/they bring texts that
 * both sides already saw on the match card, plus the contact's display name. Never the "why" text.
 */
export function introDraft(i: IntroInput): string {
  const mine = items(i.youBring)
  const theirs = items(i.theyBring)
  const topics = i.sharedTopics.map(clip).filter(Boolean).slice(0, MAX_ITEMS)
  const lines = [`Dear ${i.contactName},`, '', 'Lattice Scout suggested that we get in touch, and we both said yes.']
  if (topics.length) lines.push('', `We seem to share an interest in ${topics.join(', ')}.`)
  if (mine.length) lines.push('', 'What I could bring:', ...mine)
  if (theirs.length) lines.push('', 'What I understand you could bring:', ...theirs)
  lines.push('', 'Would you be open to a short call to see whether a collaboration makes sense?', '', 'Best wishes')
  if (i.myName?.trim()) lines.push(i.myName.trim())
  return lines.join('\n')
}
