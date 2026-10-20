"""Researcher aggregate: claimed OpenAlex records, verification level and import state."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from scout.profile.domain.grouping import AuthorSnapshot, RecordBasis
from scout.profile.domain.names import names_compatible
from scout.profile.domain.topics import ResearcherTopic
from scout.shared import AccountId, AggregateRoot, DomainEvent, InvariantViolation, ValidationFailed
from scout.shared.ids import UuidGenerator


class ImportStatus(StrEnum):
    """State of the works import."""

    QUEUED = "queued"
    IMPORTING = "importing"
    DONE = "done"
    FAILED = "failed"


class VerifiedLevel(StrEnum):
    """How sure we are that the account owns the profile."""

    SELF_CLAIMED = "self_claimed"
    ORCID_MATCHED = "orcid_matched"
    CONTESTED = "contested"


class ConflictingOrcids(InvariantViolation):
    """The claimed records carry different ORCIDs."""

    code = "conflicting_orcids"
    title = "The selected records belong to different ORCIDs"


class ProfileClaimed(DomainEvent):
    """A researcher claimed OpenAlex records."""

    name: ClassVar[str] = "profile.claimed"
    researcher_id: UUID
    account_id: AccountId
    author_ids: tuple[str, ...]
    generation: int


@dataclass
class ResearcherOpenAlexId:
    """One claimed OpenAlex author record."""

    author_id: str
    merged: bool
    basis: RecordBasis
    display_name: str
    orcid: str | None
    works_count: int
    imported_generation: int | None = None


@dataclass
class Researcher(AggregateRoot):
    """The researcher profile of an account (one per account)."""

    id: UUID
    account_id: AccountId
    display_name: str
    created_at: datetime
    updated_at: datetime
    orcid: str | None = None
    institution: str | None = None
    country: str | None = None
    career_stage: str | None = None
    verified_level: VerifiedLevel = VerifiedLevel.SELF_CLAIMED
    openalex_ids: list[ResearcherOpenAlexId] = field(default_factory=list)
    topics: list[ResearcherTopic] = field(default_factory=list)
    import_status: ImportStatus = ImportStatus.QUEUED
    import_generation: int = 0
    import_error: str | None = None
    import_requested_at: datetime | None = None
    imported_at: datetime | None = None

    @property
    def author_ids(self) -> tuple[str, ...]:
        """Claimed ids, primary first."""
        return tuple(r.author_id for r in self.openalex_ids)

    @property
    def primary_author_id(self) -> str:
        """The primary (non-merged) record id."""
        return next(r.author_id for r in self.openalex_ids if not r.merged)

    @staticmethod
    def _build(records: Sequence[AuthorSnapshot], primary_id: str) -> list[ResearcherOpenAlexId]:
        primary = next((r for r in records if r.id == primary_id), None)
        if primary is None:
            raise ValidationFailed("Primary record is not among the selected records")
        orcids = {r.orcid for r in records if r.orcid}
        if len(orcids) > 1:
            raise ConflictingOrcids("Records with different ORCIDs cannot be merged")
        out = [
            ResearcherOpenAlexId(
                primary.id,
                False,
                RecordBasis.PRIMARY,
                primary.display_name,
                primary.orcid,
                primary.works_count,
            )
        ]
        pinst = {i.casefold() for i in primary.institutions}
        for r in records:
            if r.id == primary_id:
                continue
            if r.orcid and r.orcid == primary.orcid:
                basis = RecordBasis.ORCID
            elif (
                not r.orcid
                and pinst & {i.casefold() for i in r.institutions}
                and names_compatible(primary.display_name, r.display_name)
            ):
                basis = RecordBasis.INSTITUTION_NAME
            else:
                basis = RecordBasis.MANUAL
            out.append(ResearcherOpenAlexId(r.id, True, basis, r.display_name, r.orcid, r.works_count))
        return out

    @classmethod
    def claim(
        cls,
        id: UUID,
        account_id: AccountId,
        records: Sequence[AuthorSnapshot],
        primary_id: str,
        now: datetime,
    ) -> Researcher:
        """New researcher from selected records (import generation 1, queued)."""
        ids = cls._build(records, primary_id)
        primary = records[[r.id for r in records].index(primary_id)]
        r = cls(
            id=id, account_id=account_id, display_name=primary.display_name, created_at=now, updated_at=now,
            orcid=primary.orcid, openalex_ids=ids,
        )  # fmt: skip
        r._bump(now)
        r.recompute_verification(contested=False)
        return r

    def same_claim(self, author_ids: Sequence[str], primary_id: str) -> bool:
        """Is this exactly the current claim?"""
        return set(author_ids) == set(self.author_ids) and primary_id == self.primary_author_id

    def reclaim(self, records: Sequence[AuthorSnapshot], primary_id: str, now: datetime) -> None:
        """Replace the claimed records (new import generation)."""
        self.openalex_ids = self._build(records, primary_id)
        primary = next(r for r in records if r.id == primary_id)
        self.display_name, self.orcid = primary.display_name, primary.orcid
        self._bump(now)
        self.recompute_verification(contested=False)

    def _bump(self, now: datetime) -> None:
        self.import_generation += 1
        self.import_status, self.import_error, self.import_requested_at = ImportStatus.QUEUED, None, now
        self.updated_at = now
        self.record_event(
            ProfileClaimed(
                event_id=UuidGenerator().new(), occurred_at=now, researcher_id=self.id, account_id=self.account_id,
                author_ids=self.author_ids, generation=self.import_generation,
            )
        )  # fmt: skip

    def recompute_verification(self, *, contested: bool) -> None:
        """Contested wins; else ORCID-matched when the primary has an ORCID and all records share it."""
        if contested:
            self.verified_level = VerifiedLevel.CONTESTED
            return
        primary = next(r for r in self.openalex_ids if not r.merged)
        ok = primary.orcid is not None and all(
            r.basis in (RecordBasis.PRIMARY, RecordBasis.ORCID) for r in self.openalex_ids
        )
        self.verified_level = VerifiedLevel.ORCID_MATCHED if ok else VerifiedLevel.SELF_CLAIMED

    def start_import(self, generation: int, now: datetime) -> bool:
        """Mark importing; False for a stale generation."""
        if generation != self.import_generation:
            return False
        self.import_status, self.updated_at = ImportStatus.IMPORTING, now
        return True

    def finish_import(
        self,
        generation: int,
        topics: list[ResearcherTopic],
        institution: str | None,
        country: str | None,
        now: datetime,
    ) -> bool:
        """Mark done with computed topics; False for a stale generation."""
        if generation != self.import_generation:
            return False
        self.topics, self.institution, self.country = topics, institution, country
        self.import_status, self.import_error, self.imported_at, self.updated_at = (
            ImportStatus.DONE,
            None,
            now,
            now,
        )
        return True

    def fail_import(self, generation: int, code: str, now: datetime) -> bool:
        """Mark failed; False for a stale generation."""
        if generation != self.import_generation:
            return False
        self.import_status, self.import_error, self.updated_at = ImportStatus.FAILED, code, now
        return True
