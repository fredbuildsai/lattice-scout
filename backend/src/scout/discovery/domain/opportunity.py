"""``Opportunity`` with its claims and evidence, the show rule and the user-facing visibility rules."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from scout.discovery.domain.enums import (
    CheckCode,
    ClaimSection,
    ClaimStatus,
    OppKind,
    OppStatus,
    SetBy,
    SourceType,
)
from scout.shared import AccountId, AggregateRoot, InvalidStateTransition, InvariantViolation

USER_STATUSES = frozenset({OppStatus.SHOWN, OppStatus.SAVED, OppStatus.DISMISSED})
MIN_VERIFIED_EVIDENCE = 2
_TARGET = {
    SourceType.LITERATURE: "corpus_work_id", SourceType.PROFILE: "corpus_work_id",
    SourceType.VAULT: "chunk_id", SourceType.COUNT: "probe_id",
}  # fmt: skip


@dataclass
class Evidence:
    """One citation of a claim. ``verified`` is ``None`` until checked, never ``True`` unless it resolves."""

    id: UUID
    ord: int
    source_type: SourceType
    label: str  # the id the model used (``W123``, ``d1.c12``, ``n1``...)
    ref: str  # canonical reference: OpenAlex id, chunk id, insight ref or probe label
    quote: str | None = None
    verified: bool | None = None
    check: CheckCode | None = None
    check_detail: str | None = None
    corpus_work_id: UUID | None = None
    chunk_id: UUID | None = None
    document_id: UUID | None = None
    probe_id: UUID | None = None
    insight_ref: str | None = None

    def mark_verified(self, check_detail: str | None = None) -> None:
        """Mark the citation as resolved and matching.

        Raises:
            InvariantViolation: the evidence does not point at anything in the run (no work, chunk or probe id).
        """
        if getattr(self, _TARGET[self.source_type]) is None:
            raise InvariantViolation(f"{self.source_type.value} evidence has no target to verify against")
        self.verified, self.check, self.check_detail = True, CheckCode.OK, check_detail

    def mark_failed(self, check: CheckCode, detail: str | None = None) -> None:
        """Record a failed check. ``verified`` becomes ``False``."""
        self.verified, self.check, self.check_detail = False, check, detail


@dataclass
class Claim:
    """One statement of an opportunity. Only ``verified`` claims are ever shown."""

    id: UUID
    ord: int
    section: ClaimSection
    text: str
    status: ClaimStatus = ClaimStatus.PENDING
    status_reason: str | None = None
    set_by: SetBy = SetBy.ANALYSE
    agent: str | None = None
    prompt_version: str | None = None
    model: str | None = None
    critic_verdict: str | None = None
    critic_reason: str | None = None
    critic_fix: str | None = None
    evidence: list[Evidence] = field(default_factory=list)

    def set_status(self, status: ClaimStatus, reason: str | None, set_by: SetBy) -> None:
        """Move forward only: ``pending`` to any other status, or ``verified`` to ``rejected_by_critic``.

        Raises:
            InvalidStateTransition: any other change.
        """
        allowed = (self.status is ClaimStatus.PENDING and status is not ClaimStatus.PENDING) or (
            self.status is ClaimStatus.VERIFIED and status is ClaimStatus.REJECTED_BY_CRITIC
        )
        if not allowed:
            raise InvalidStateTransition(
                "claim_status", extra={"from": self.status.value, "to": status.value}
            )
        self.status, self.status_reason, self.set_by = status, reason, set_by

    def verified_evidence(self) -> list[Evidence]:
        """Evidence with ``verified is True``."""
        return [e for e in self.evidence if e.verified is True]


@dataclass(frozen=True)
class Scores:
    """Opportunity scores, each in [0, 1]."""

    fit: float
    novelty: float
    readiness: float
    effort: float
    rank: float

    def __post_init__(self) -> None:
        for name in ("fit", "novelty", "readiness", "effort", "rank"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise InvariantViolation(f"score {name} must be between 0 and 1")


@dataclass
class Opportunity(AggregateRoot):
    """A candidate research opportunity with the evidence for it."""

    id: UUID
    run_id: UUID
    owner_id: AccountId
    batch: int
    ord: int
    kind: OppKind
    title: str
    one_line: str
    created_at: datetime
    status: OppStatus = OppStatus.DRAFT
    status_reason: str | None = None
    status_step: str | None = None
    duplicate_of: UUID | None = None
    cluster_ids: tuple[UUID, ...] = ()
    scores: Scores | None = None
    score_basis: dict[str, Any] = field(default_factory=dict)
    novelty_probe_id: UUID | None = None
    readiness_checklist: list[dict[str, Any]] = field(default_factory=list)
    effort_estimate: str | None = None
    next_steps: list[str] = field(default_factory=list)
    analyst_version: str | None = None
    critic_version: str | None = None
    model: str | None = None
    verify_state: str = "pending"  # pending | done
    claims: list[Claim] = field(default_factory=list)

    def visible_claims(self) -> list[Claim]:
        """Verified claims only, each with its verified evidence only. Nothing else ever reaches a user."""
        return [
            replace(c, evidence=c.verified_evidence())
            for c in self.claims
            if c.status is ClaimStatus.VERIFIED
        ]

    def show_decision(self) -> tuple[bool, str]:
        """Whether the opportunity may be shown, and the reason when not (DECISIONS 59).

        Shown only when: at least two distinct verified evidence items (by source type and reference) on verified
        rationale or objection claims, at least one of them external literature; and a measured novelty count
        (``scores.novelty`` plus a verified ``score`` claim with verified ``count`` evidence).
        """
        items: set[tuple[SourceType, str]] = set()
        measured = False
        for c in self.claims:
            if c.status is not ClaimStatus.VERIFIED:
                continue
            if c.section is ClaimSection.SCORE:
                measured = measured or any(e.source_type is SourceType.COUNT for e in c.verified_evidence())
            else:
                items.update((e.source_type, e.ref) for e in c.verified_evidence())
        if len(items) < MIN_VERIFIED_EVIDENCE:
            return False, "insufficient_evidence"
        if not any(t is SourceType.LITERATURE for t, _ in items):
            return False, "no_literature"
        if not (measured and self.scores is not None):
            return False, "no_novelty_measure"
        return True, "ok"

    def set_user_status(self, new: OppStatus) -> None:
        """The owner's choice among ``shown``, ``saved`` and ``dismissed`` (any order).

        Raises:
            InvalidStateTransition: the opportunity is not user-visible, or ``new`` is not a user status.
        """
        if self.status not in USER_STATUSES or new not in USER_STATUSES:
            raise InvalidStateTransition(
                "opportunity_status", extra={"from": self.status.value, "to": new.value}
            )
        self.status = new


__all__ = [
    "MIN_VERIFIED_EVIDENCE", "USER_STATUSES", "Claim", "Evidence", "InvalidStateTransition", "Opportunity", "Scores",
]  # fmt: skip
