"""Anti-corruption layer: the feedback context asks the owners of a target whether it may be rated."""

from __future__ import annotations

from uuid import UUID

from scout.discovery.application.service import DiscoveryService
from scout.feedback.domain.feedback import TargetType
from scout.matching.application.service import MatchingService
from scout.shared import AccountId


class TargetCheckAdapter:
    """``TargetCheck`` over the discovery and matching application services (no table of theirs is read)."""

    def __init__(self, discovery: DiscoveryService, matching: MatchingService) -> None:
        self._discovery, self._matching = discovery, matching

    async def rateable(self, author_id: AccountId, target_type: TargetType, target_id: UUID) -> bool:
        """Opportunities: the author's own and visible under the evidence rule. Matches: mutual ones of the author."""
        if target_type is TargetType.OPPORTUNITY:
            return await self._discovery.is_visible(author_id, target_id)
        return await self._matching.is_mutual(author_id, target_id)
