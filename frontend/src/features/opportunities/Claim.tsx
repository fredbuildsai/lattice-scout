import { SourceMarker } from './SourceMarker'
import type { ClaimData } from './api'

/**
 * A claim: its text followed by numbered `<SourceMarker>`s, one per verified citation.
 * `opportunityId` is used for analytics. `className` styles the text wrapper.
 */
export function Claim({ claim, opportunityId, className = '' }: { claim: ClaimData; opportunityId: string; className?: string }) {
  return (
    <span className={className}>
      {claim.text}
      {claim.markers.map((m, i) => (
        <SourceMarker key={m.id} evidence={m} index={i + 1} opportunityId={opportunityId} claimId={claim.id} />
      ))}
    </span>
  )
}
