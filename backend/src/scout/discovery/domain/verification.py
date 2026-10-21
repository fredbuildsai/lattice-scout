"""Deterministic checks of evidence references and claims against what the run actually holds (plan task 3.7).

Nothing here calls a model or the network: a citation passes only if the cited object exists in the run (or is
resolvable) and the quote is verbatim in its text after normalisation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from scout.discovery.domain.enums import CheckCode, CorpusOrigin
from scout.shared import AccountId, InvariantViolation
from scout.shared.text import norm

MIN_QUOTE = 15
MAX_QUOTE = 300
COUNT_TOLERANCE = 0.05
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_UNITS = r"(?:stud|paper|work|publication|article)\w*"
NOVELTY_WORDING = re.compile(
    rf"\bfew {_UNITS}|\bno {_UNITS}|\bonly \d+ {_UNITS}|\black of (?:research|work|stud\w*|literature|evidence)"
    r"|\blittle (?:work|research|literature|evidence)|\bscarc\w*"
    r"|\bnot (?:yet )?been (?:studied|explored|investigated|addressed)|\bunder-?(?:studied|explored)\b|\bunexplored\b",
    re.IGNORECASE,
)
_INTEGER = re.compile(r"\d+")


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
    if ref.type == "work":
        return _check_work(ref, index)
    if ref.type == "chunk":
        return _check_chunk(ref, index)
    if ref.type == "insight":
        ok = ref.id in index.insights
        return CheckResult(CheckCode.OK if ok else CheckCode.UNKNOWN_INSIGHT)
    p = index.probes.get(ref.id)
    ok = p is not None and p.measured and p.count is not None
    return CheckResult(CheckCode.OK if ok else CheckCode.UNKNOWN_COUNT)


def _quote_shape(quote: str | None) -> tuple[str, CheckResult | None]:
    """Normalise the quote and refuse it when missing or outside the length bounds."""
    if quote is None:
        return "", CheckResult(CheckCode.QUOTE_MISSING)
    q = norm(quote)
    if len(q) < MIN_QUOTE:
        return q, CheckResult(CheckCode.QUOTE_TOO_SHORT)
    if len(q) > MAX_QUOTE:
        return q, CheckResult(CheckCode.QUOTE_TOO_LONG)
    return q, None


def _found(q: str, *texts: str | None) -> CheckResult:
    if any(t is not None and q in norm(t) for t in texts):
        return CheckResult(CheckCode.OK)
    return CheckResult(CheckCode.QUOTE_NOT_FOUND)


def _check_work(ref: EvidenceRef, index: RunMaterialIndex) -> CheckResult:
    work = index.works.get(ref.id)
    if work is None:
        return CheckResult(CheckCode.UNKNOWN_WORK)
    if work.origin is CorpusOrigin.PROFILE:
        if ref.quote is None:
            return CheckResult(CheckCode.OK)
        return _found(norm(ref.quote), work.title, work.abstract)
    q, bad = _quote_shape(ref.quote)
    if bad is not None:
        return bad
    if not work.abstract:
        return CheckResult(CheckCode.NO_ABSTRACT)
    return _found(q, work.abstract)


def _check_chunk(ref: EvidenceRef, index: RunMaterialIndex) -> CheckResult:
    chunk = index.chunks.get(ref.id)
    if chunk is None:
        return CheckResult(CheckCode.UNKNOWN_CHUNK)
    if chunk.owner_id != index.owner_id:
        raise InvariantViolation("evidence points at another owner's passage")
    if not chunk.active:
        return CheckResult(CheckCode.SOURCE_DELETED)
    q, bad = _quote_shape(ref.quote)
    if bad is not None:
        return bad
    if chunk.retention == "keep":
        return _found(q, chunk.text)
    return _found(q, *chunk.quotes)  # one retained quote at a time: never stitched


def check_claim(
    text: str, results: Sequence[tuple[EvidenceRef, CheckResult]], index: RunMaterialIndex
) -> CheckCode:
    """Claim-level code after the evidence checks.

    Returns the first of: ``no_evidence`` (no references), ``personal_data`` (an e-mail address),
    ``novelty_without_count`` (novelty wording without a cited count), ``count_mismatch`` (a cited count and an
    integer of 10 or more in the text that is not within 5 percent of it), a failing evidence code when none of the
    references is ok, otherwise ``ok``.
    """
    if not results:
        return CheckCode.NO_EVIDENCE
    if EMAIL.search(text):
        return CheckCode.PERSONAL_DATA
    counts = [
        p.count
        for ref, res in results
        if ref.type == "count" and res.ok and (p := index.probes.get(ref.id)) and p.count is not None
    ]
    if NOVELTY_WORDING.search(text) and not counts:
        return CheckCode.NOVELTY_WITHOUT_COUNT
    numbers = [int(n) for n in _INTEGER.findall(text) if int(n) >= 10]
    if counts and numbers and not any(abs(n - c) <= COUNT_TOLERANCE * c for n in numbers for c in counts):
        return CheckCode.COUNT_MISMATCH
    if not any(res.ok for _, res in results):
        return results[0][1].code
    return CheckCode.OK


def claim_passes(code: CheckCode) -> bool:
    """A claim passes the code stage when ``check_claim`` returned ``ok``."""
    return code is CheckCode.OK
