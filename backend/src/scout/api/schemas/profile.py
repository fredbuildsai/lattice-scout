"""DTOs for identity and profile endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TopicOut(BaseModel):
    """A topic with a weight between 0 and 1."""

    topic_id: str = Field(description="OpenAlex topic id, e.g. T10610.")
    name: str | None = Field(default=None, description="Topic name.")
    subfield: str | None = Field(default=None, description="Subfield name.")
    weight: float = Field(ge=0, le=1, description="Relative weight; the largest topic is 1.")


class RecordOut(BaseModel):
    """One OpenAlex author record inside a candidate group."""

    author_id: str = Field(description="OpenAlex author id, e.g. A5059588273.")
    display_name: str = Field(description="Name as recorded in OpenAlex.")
    orcid: str | None = Field(default=None, description="Bare ORCID.")
    works_count: int = Field(description="Works attributed to this record.")
    institutions: list[str] = Field(description="Last known institutions.")
    basis: str = Field(description="Why the record is in the group: primary, orcid or institution_name.")


class CandidateGroupOut(BaseModel):
    """A candidate profile: merged records plus weakly linked ones the user may add."""

    group_id: str = Field(description="Id of the primary record.")
    primary: RecordOut = Field(description="The record with the most works.")
    records: list[RecordOut] = Field(description="Merged records (shared ORCID), primary first.")
    possible: list[RecordOut] = Field(
        description="Possible further records; shown unticked, never merged automatically."
    )
    includes_records: int = Field(description="Number of merged records.")
    works_count: int = Field(description="Sum of works over merged records.")
    cited_by_count: int = Field(description="Sum of citations over merged records.")
    orcid: str | None = Field(default=None, description="ORCID of the primary record.")
    institutions: list[str] = Field(description="Institutions across merged records.")
    country: str | None = Field(default=None, description="Country code of the primary record.")
    top_topics: list[TopicOut] = Field(description="Strongest topics across merged records.")


class CandidatesOut(BaseModel):
    """Result of a candidate search."""

    groups: list[CandidateGroupOut] = Field(description="Candidate profiles, best match first.")


class ClaimIn(BaseModel):
    """Claim one or more OpenAlex records as your profile."""

    model_config = ConfigDict(extra="forbid")

    author_ids: list[str] = Field(min_length=1, max_length=10, description="OpenAlex author ids to merge.")
    primary_author_id: str | None = Field(
        default=None, description="Primary record; defaults to the first id."
    )


class ImportOut(BaseModel):
    """State of the works import."""

    status: str = Field(description="queued, importing, done or failed.")
    generation: int = Field(description="Increases with every new claim.")
    error_code: str | None = Field(default=None, description="Set when status is failed.")
    requested_at: datetime | None = Field(default=None, description="When the import was requested.")
    finished_at: datetime | None = Field(default=None, description="When the import finished.")


class OpenAlexIdOut(BaseModel):
    """A claimed OpenAlex record."""

    author_id: str = Field(description="OpenAlex author id.")
    merged: bool = Field(description="True for non-primary records.")
    basis: str = Field(description="primary, orcid, institution_name or manual.")
    works_count: int = Field(description="Works attributed to this record.")


class WorkOut(BaseModel):
    """A work attributed to the researcher."""

    id: UUID = Field(description="Id of the claimed work.")
    openalex_work_id: str = Field(description="OpenAlex work id.")
    doi: str | None = Field(default=None, description="Bare DOI.")
    title: str = Field(description="Title.")
    year: int | None = Field(default=None, description="Publication year.")
    cited_by: int = Field(description="Citation count.")
    type: str | None = Field(default=None, description="OpenAlex work type.")
    included: bool = Field(description="Whether the work counts for gap analysis and matching.")
    included_by_user: bool = Field(description="True if the user ticked or unticked it.")
    uncertain_reason: str | None = Field(default=None, description="Why the work started unticked.")


class WorksSummaryOut(BaseModel):
    """Counts of works."""

    total: int = Field(description="All claimed works.")
    included: int = Field(description="Included works.")
    uncertain: int = Field(description="Works that started unticked.")


class ProfileOut(BaseModel):
    """The caller's researcher profile."""

    researcher_id: UUID = Field(description="Researcher id.")
    display_name: str = Field(description="Name of the primary record.")
    orcid: str | None = Field(default=None, description="ORCID of the primary record.")
    institution: str | None = Field(default=None, description="Institution (after import).")
    country: str | None = Field(default=None, description="Country code (after import).")
    verified_level: str = Field(description="self_claimed or orcid_matched (conflicts are never shown).")
    import_: ImportOut = Field(alias="import", description="Import state; poll while queued or importing.")
    openalex_ids: list[OpenAlexIdOut] = Field(description="Claimed OpenAlex records.")
    topics: list[TopicOut] = Field(description="Topics computed from included works.")
    works_summary: WorksSummaryOut = Field(description="Counts of works.")
    works: list[WorkOut] = Field(description="Claimed works, newest first.")

    model_config = ConfigDict(populate_by_name=True)


class WorkPatchIn(BaseModel):
    """Include or exclude a work."""

    model_config = ConfigDict(extra="forbid")

    included: bool = Field(description="True to include the work.")


class ResearcherSummaryOut(BaseModel):
    """Short researcher summary for ``GET /me``."""

    researcher_id: UUID = Field(description="Researcher id.")
    display_name: str = Field(description="Name of the primary record.")
    verified_level: str = Field(description="self_claimed or orcid_matched (conflicts are never shown).")
    import_status: str = Field(description="queued, importing, done or failed.")


class MeOut(BaseModel):
    """The signed-in account."""

    account_id: UUID = Field(description="Account id (= auth user id).")
    email: str = Field(description="E-mail address.")
    role: str = Field(description="user or admin.")
    launch_field: str = Field(description="Launch field of the pilot.")
    consent_matching: bool = Field(description="Whether the user agreed to be matched.")
    consent_vault_signals: bool = Field(description="Whether vault-derived signals may be used for matching.")
    created_at: datetime = Field(description="Account creation time.")
    researcher: ResearcherSummaryOut | None = Field(default=None, description="Profile summary, if claimed.")


class ConsentIn(BaseModel):
    """Partial update of consents."""

    model_config = ConfigDict(extra="forbid")

    consent_matching: bool | None = Field(
        default=None, description="Agree to be matched with other researchers."
    )
    consent_vault_signals: bool | None = Field(
        default=None, description="Allow vault-derived signals in matching."
    )
