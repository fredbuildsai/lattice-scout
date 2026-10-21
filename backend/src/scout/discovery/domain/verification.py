"""Deterministic checks of evidence references and claims against what the run actually holds (plan task 3.7).

Nothing here calls a model or the network: a citation passes only if the cited object exists in the run (or is
resolvable) and the quote is verbatim in its text after normalisation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from scout.discovery.domain.enums import CheckCode, CorpusOrigin
from scout.shared import AccountId

MIN_QUOTE = 15
MAX_QUOTE = 300
COUNT_TOLERANCE = 0.05


@dataclass(frozen=True)
class EvidenceRef:
    """A citation as the analyst wrote it. ``type`` is ``work``, ``chunk``, ``insight`` or ``count``."""

    type: Literal["work", "chunk", "insight", "count"]
    id: str
    quote: str | None = None


@dataclass(frozen=True)
class WorkMaterial:
    """A work of the run corpus (public bibliographic data)."""

    openalex_id: str
    title: str
    abstract: str | None
    origin: CorpusOrigin


@dataclass(frozen=True)
class ChunkMaterial:
    """A cited vault passage. ``text`` is set for ``keep`` documents; ``quotes`` holds the retained quotes of a
    ``discard`` document (full text no longer exists)."""

    label: str
    chunk_id: UUID
    owner_id: AccountId
    active: bool
    retention: Literal["keep", "discard"]
    text: str | None = None
    quotes: tuple[str, ...] = ()


@dataclass(frozen=True)
class InsightMaterial:
    """A verified vault insight the analyst may cite by reference."""

    ref: str
    text: str
    chunk_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProbeMaterial:
    """A novelty probe: the query and its measured count."""

    label: str
    query: str
    count: int | None
    measured: bool


@dataclass(frozen=True)
class RunMaterialIndex:
    """Everything a citation can resolve to in this run."""

    owner_id: AccountId
    works: Mapping[str, WorkMaterial] = field(default_factory=dict)
    chunks: Mapping[str, ChunkMaterial] = field(default_factory=dict)
    insights: Mapping[str, InsightMaterial] = field(default_factory=dict)
    probes: Mapping[str, ProbeMaterial] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckResult:
    """Outcome of checking one reference."""

    code: CheckCode
    detail: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the reference resolved and its quote matched."""
        return self.code is CheckCode.OK


def check_ref(ref: EvidenceRef, index: RunMaterialIndex) -> CheckResult:
    """Check one citation.

    * ``work``: the id must be in the corpus (any origin). Literature needs a verbatim quote of 15 to 300
      characters found in the abstract; a profile work's quote is optional and may come from title or abstract.
    * ``chunk``: the label must be in the index and active; ``keep`` passages are matched against their text, a
      ``discard`` quote must be a substring of one retained quote (quotes are never stitched together).
    * ``insight``: must be one of the run's verified insights.
    * ``count``: the probe must exist and be measured with a count.

    Raises:
        InvariantViolation: the chunk belongs to another owner (a bug or an attack, never a model slip).
    """
    raise NotImplementedError


def check_claim(
    text: str, results: Sequence[tuple[EvidenceRef, CheckResult]], index: RunMaterialIndex
) -> CheckCode:
    """Claim-level code after the evidence checks.

    Returns the first of: ``no_evidence`` (no references), ``personal_data`` (an e-mail address),
    ``novelty_without_count`` (novelty wording without a cited count), ``count_mismatch`` (a cited count and an
    integer of 10 or more in the text that is not within 5 percent of it), a failing evidence code when none of the
    references is ok, otherwise ``ok``.
    """
    raise NotImplementedError


def claim_passes(code: CheckCode) -> bool:
    """A claim passes the code stage when ``check_claim`` returned ``ok``."""
    return code is CheckCode.OK
