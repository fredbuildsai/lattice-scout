"""Run corpus entities: works, clusters and novelty probes (all belong to one run)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from scout.discovery.domain.enums import CorpusOrigin


@dataclass(frozen=True)
class CorpusWork:
    """A work the analysis may cite. Public bibliographic data only; author names are display-only."""

    id: UUID
    run_id: UUID
    openalex_id: str
    title: str
    abstract: str | None
    year: int | None
    cited_by: int
    type: str | None
    doi: str | None
    topics: tuple[dict[str, Any], ...]  # {id, name, subfield}
    primary_topic_id: str | None
    authors: tuple[str, ...]
    origin: CorpusOrigin
    query_labels: tuple[str, ...] = ()
    cluster_id: UUID | None = None


@dataclass
class Cluster:
    """A group of corpus works around one OpenAlex topic."""

    id: UUID
    run_id: UUID
    key: str
    label_key: str  # k1, k2... (misc for the leftovers)
    label: str
    description: str
    search_terms: list[str]
    label_source: Literal["topic", "llm"]
    topic_ids: list[str]
    subfield: str | None
    size: int
    is_misc: bool = False
    user_works: int = 0
    insight_weight: float = 0.0
    user_coverage: float = 0.0
    density: float = 0.0
    candidate_rank: int | None = None


@dataclass
class NoveltyProbe:
    """A count query that measures how much literature matches an idea."""

    id: UUID
    run_id: UUID
    label: str  # n1, n2...
    cluster_id: UUID | None
    query: str
    from_date: date
    count: int | None = None
    status: Literal["pending", "measured", "skipped"] = "pending"
    measured_at: datetime | None = None
