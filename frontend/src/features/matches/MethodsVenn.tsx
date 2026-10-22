type Props = {
  youLabel: string
  theyLabel: string
  sharedTopics: string[]
  youBring: { text: string }[]
  theyBring: { text: string }[]
}

/**
 * Venn diagram of the two sets of methods around the shared topics. Shows only what the match card already exposes
 * (no weights, no counts). The whole figure has a textual alternative that spells out all three sets.
 */
export function MethodsVenn({ youLabel, theyLabel, sharedTopics, youBring, theyBring }: Props) {
  const overlap = sharedTopics.length > 0
  const list = (xs: string[]) => (xs.length ? xs.join('; ') : 'nothing listed')
  const alt = `Venn diagram. You bring: ${list(youBring.map((b) => b.text))}. They bring: ${list(theyBring.map((b) => b.text))}. Shared topics: ${overlap ? sharedTopics.join(', ') : 'none'}.`
  const dx = overlap ? 36 : 70
  return (
    <svg viewBox="0 0 220 130" role="img" aria-label={alt} className="h-auto w-full max-w-[260px] text-scout-ink">
      <circle
        cx={110 - dx}
        cy="65"
        r="52"
        fill="currentColor"
        fillOpacity="0.08"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <circle
        cx={110 + dx}
        cy="65"
        r="52"
        fill="currentColor"
        fillOpacity="0.14"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <text
        x={110 - dx - (overlap ? 22 : 0)}
        y="69"
        textAnchor="middle"
        fontSize="12"
        fontWeight="600"
        fill="currentColor"
      >
        {youLabel.slice(0, 10)}
      </text>
      <text
        x={110 + dx + (overlap ? 22 : 0)}
        y="69"
        textAnchor="middle"
        fontSize="12"
        fontWeight="600"
        fill="currentColor"
      >
        {theyLabel.slice(0, 10)}
      </text>
      {overlap && (
        <text x="110" y="69" textAnchor="middle" fontSize="10" fill="currentColor">
          Shared
        </text>
      )}
    </svg>
  )
}
