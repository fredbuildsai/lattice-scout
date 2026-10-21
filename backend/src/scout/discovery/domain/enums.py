"""Discovery enumerations."""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    """Lifecycle of an analysis run (DECISIONS 56)."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStep(StrEnum):
    """Pipeline steps, in execution order."""

    PLAN_QUERIES = "plan_queries"
    RETRIEVE = "retrieve"
    GROUP_BY_TOPIC = "group_by_topic"
    COVERAGE = "coverage"
    MEASURE_NOVELTY = "measure_novelty"
    ANALYSE = "analyse"
    VERIFY = "verify"
    SCORE_PERSIST = "score_persist"


class StepStatus(StrEnum):
    """State of one step."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    DEGRADED = "degraded"


class OppKind(StrEnum):
    """Kinds of opportunity."""

    WHITE_SPOT = "white_spot"
    SYNERGY = "synergy"
    RESUBMISSION = "resubmission"


class OppStatus(StrEnum):
    """Opportunity status. Only ``shown``, ``saved`` and ``dismissed`` are ever visible to users."""

    DRAFT = "draft"
    SHOWN = "shown"
    SAVED = "saved"
    DISMISSED = "dismissed"
    REJECTED_BY_CRITIC = "rejected_by_critic"
    UNVERIFIED = "unverified"
    DUPLICATE = "duplicate"


class ClaimStatus(StrEnum):
    """Claim status. Only ``verified`` claims are ever visible to users."""

    PENDING = "pending"
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONTRADICTED = "contradicted"
    REJECTED_BY_CRITIC = "rejected_by_critic"


class ClaimSection(StrEnum):
    """Where a claim appears on the opportunity page."""

    RATIONALE = "rationale"
    OBJECTION = "objection"
    SCORE = "score"


class SourceType(StrEnum):
    """What an evidence item points at."""

    LITERATURE = "literature"
    PROFILE = "profile"
    VAULT = "vault"
    COUNT = "count"


class SetBy(StrEnum):
    """Who set the current claim status."""

    ANALYSE = "analyse"
    VERIFY_CODE = "verify_code"
    VERIFY_CRITIC = "verify_critic"
    SCORE_PERSIST = "score_persist"


class CorpusOrigin(StrEnum):
    """How a work entered the run corpus."""

    SEARCH = "search"
    PROFILE = "profile"
    RESOLVED = "resolved"


class CheckCode(StrEnum):
    """Result codes of the deterministic checks (and later stages) on evidence and claims."""

    OK = "ok"
    UNKNOWN_WORK = "unknown_work"
    UNKNOWN_CHUNK = "unknown_chunk"
    UNKNOWN_INSIGHT = "unknown_insight"
    UNKNOWN_COUNT = "unknown_count"
    QUOTE_MISSING = "quote_missing"
    QUOTE_TOO_SHORT = "quote_too_short"
    QUOTE_TOO_LONG = "quote_too_long"
    QUOTE_NOT_FOUND = "quote_not_found"
    NO_ABSTRACT = "no_abstract"
    SOURCE_DELETED = "source_deleted"
    NO_EVIDENCE = "no_evidence"
    NOVELTY_WITHOUT_COUNT = "novelty_without_count"
    COUNT_MISMATCH = "count_mismatch"
    PERSONAL_DATA = "personal_data"
    CRITIC_NO_VERDICT = "critic_no_verdict"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CRITIC_UNAVAILABLE = "critic_unavailable"


class RunFailure(StrEnum):
    """Why a run failed."""

    BUDGET_EXHAUSTED = "budget_exhausted"
    CORPUS_EMPTY = "corpus_empty"
    PROFILE_MISSING = "profile_missing"
    LLM_UNAVAILABLE = "llm_unavailable"
    LITERATURE_UNAVAILABLE = "literature_unavailable"
    STALLED = "stalled"
    INTERNAL_ERROR = "internal_error"
