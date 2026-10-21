"""Claimed works and the initial inclusion heuristic."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from scout.profile.domain.grouping import TopicWeight
from scout.profile.domain.names import names_compatible

ARTICLE_TYPES = frozenset({"article", "review"})


class UncertainReason(StrEnum):
    """Why a work starts unticked."""

    NON_ARTICLE_TYPE = "non_article_type"
    NAME_MISMATCH = "name_mismatch"


def initial_inclusion(
    work_type: str | None, names_at_claimed_positions: Iterable[str], variants: Iterable[str]
) -> UncertainReason | None:
    """Return why a work is uncertain, or None if it should start included.

    Absent author names (truncated authorships) never count against a work.
    """
    if work_type not in ARTICLE_TYPES:
        return UncertainReason.NON_ARTICLE_TYPE
    names, vs = [n for n in names_at_claimed_positions if n], list(variants)
    if names and not any(names_compatible(n, v) for n in names for v in vs):
        return UncertainReason.NAME_MISMATCH
    return None


@dataclass
class ClaimedWork:
    """A work attributed to a researcher; the user can include or exclude it."""

    id: UUID
    researcher_id: UUID
    openalex_work_id: str
    title: str
    doi: str | None = None
    year: int | None = None
    cited_by: int = 0
    type: str | None = None
    abstract: str | None = None
    topics: tuple[TopicWeight, ...] = ()
    source_author_ids: frozenset[str] = field(default_factory=frozenset)
    included: bool = True
    included_by_user: bool = False
    uncertain_reason: UncertainReason | None = None

    def toggle(self, included: bool) -> None:
        """User decision; survives re-imports."""
        self.included, self.included_by_user = included, True

    def refresh(self, fresh: ClaimedWork, author_id: str, reason: UncertainReason | None) -> None:
        """Update metadata from a re-import; the inclusion flag changes only if the user never touched it."""
        self.title, self.doi, self.year, self.cited_by = fresh.title, fresh.doi, fresh.year, fresh.cited_by
        self.type, self.abstract, self.topics = fresh.type, fresh.abstract, fresh.topics
        self.source_author_ids = self.source_author_ids | {author_id}
        self.uncertain_reason = reason
        if not self.included_by_user:
            self.included = reason is None
