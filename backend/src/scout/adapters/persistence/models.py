"""SQLAlchemy Core table definitions for the context repositories (schema owned by Alembic migrations)."""

from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    Date,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

metadata = MetaData()


def ts(name: str, **kw: object) -> Column[object]:
    """Timestamptz column."""
    return Column(name, TIMESTAMP(timezone=True), **kw)  # type: ignore[arg-type]


account = Table(
    "account", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("email", Text, nullable=False),
    Column("consent_matching", Boolean, nullable=False),
    Column("consent_vault_signals", Boolean, nullable=False),
    Column("launch_field", Text, nullable=False),
    Column("role", Text, nullable=False),
    ts("deleted_at"), ts("created_at", nullable=False), ts("updated_at", nullable=False),
)  # fmt: skip

audit_log = Table(
    "audit_log", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("actor_id", UUID(as_uuid=True)),
    Column("action", Text, nullable=False),
    Column("target", Text),
    ts("at", nullable=False),
    Column("meta", JSONB, nullable=False),
)  # fmt: skip

researcher = Table(
    "researcher", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("account_id", UUID(as_uuid=True), ForeignKey("account.id"), nullable=False),
    Column("display_name", Text, nullable=False),
    Column("orcid", Text), Column("institution", Text), Column("country", Text), Column("career_stage", Text),
    Column("verified_level", Text, nullable=False),
    Column("import_status", Text, nullable=False),
    Column("import_generation", Integer, nullable=False),
    Column("import_error", Text),
    ts("import_requested_at"), ts("imported_at"), ts("created_at", nullable=False), ts("updated_at", nullable=False),
)  # fmt: skip

researcher_openalex_id = Table(
    "researcher_openalex_id", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("researcher_id", UUID(as_uuid=True), ForeignKey("researcher.id"), nullable=False),
    Column("openalex_author_id", Text, nullable=False),
    Column("merged", Boolean, nullable=False),
    Column("basis", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("orcid", Text),
    Column("works_count", Integer, nullable=False),
    Column("imported_generation", Integer),
    Column("position", Integer, nullable=False),
)  # fmt: skip

researcher_topic = Table(
    "researcher_topic", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("researcher_id", UUID(as_uuid=True), ForeignKey("researcher.id"), nullable=False),
    Column("openalex_topic_id", Text, nullable=False),
    Column("name", Text), Column("subfield", Text),
    Column("weight", Float, nullable=False),
)  # fmt: skip

claimed_work = Table(
    "claimed_work", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("researcher_id", UUID(as_uuid=True), ForeignKey("researcher.id"), nullable=False),
    Column("openalex_work_id", Text, nullable=False),
    Column("doi", Text), Column("title", Text, nullable=False), Column("year", Integer),
    Column("cited_by", Integer, nullable=False), Column("type", Text), Column("abstract", Text),
    Column("topics", JSONB, nullable=False),
    Column("source_author_ids", ARRAY(Text), nullable=False),
    Column("included", Boolean, nullable=False),
    Column("included_by_user", Boolean, nullable=False),
    Column("uncertain_reason", Text),
    ts("updated_at"),
)  # fmt: skip

document = Table(
    "document", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("owner_id", UUID(as_uuid=True), ForeignKey("account.id"), nullable=False),
    Column("filename", Text, nullable=False), Column("mime", Text, nullable=False),
    Column("size_bytes", BigInteger, nullable=False), Column("sha256", Text, nullable=False),
    Column("storage_key", Text, nullable=False), Column("retention", Text, nullable=False),
    Column("kind", Text), Column("kind_suggested", Text), Column("kind_reason", Text),
    Column("status", Text, nullable=False), Column("failure_code", Text),
    Column("pages", Integer), Column("page_basis", Text), Column("chunk_count", Integer), Column("extractor", Text),
    ts("original_deleted_at"), ts("extracted_at"), ts("ready_at"), ts("deleted_at"),
    ts("created_at", nullable=False), ts("updated_at", nullable=False),
)  # fmt: skip

document_chunk = Table(
    "document_chunk", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("document_id", UUID(as_uuid=True), ForeignKey("document.id"), nullable=False),
    Column("ord", Integer, nullable=False), Column("label", Text, nullable=False),
    Column("page_from", Integer, nullable=False), Column("page_to", Integer, nullable=False),
    Column("char_start", Integer, nullable=False), Column("char_end", Integer, nullable=False),
    Column("text", Text), Column("quotes", JSONB, nullable=False), Column("summary", Text),
    ts("redacted_at"), ts("created_at", nullable=False), ts("updated_at", nullable=False),
)  # fmt: skip

document_insights = Table(
    "document_insights", metadata,
    Column("document_id", UUID(as_uuid=True), ForeignKey("document.id"), primary_key=True),
    Column("payload", JSONB, nullable=False),
    Column("verified_count", Integer, nullable=False), Column("unverified_count", Integer, nullable=False),
    Column("prompt_version", Text, nullable=False), Column("model", Text, nullable=False),
    Column("mode", Text, nullable=False), Column("slices", Integer, nullable=False),
    Column("cost_usd", Numeric(12, 8), nullable=False),
    ts("created_at", nullable=False), ts("updated_at", nullable=False),
)  # fmt: skip

analysis_run = Table(
    "analysis_run", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("owner_id", UUID(as_uuid=True), ForeignKey("account.id"), nullable=False),
    Column("session_id", UUID(as_uuid=True)),
    Column("status", Text, nullable=False), Column("step", Text),
    Column("step_state", JSONB, nullable=False), Column("inputs", JSONB, nullable=False),
    Column("budget", JSONB, nullable=False),
    Column("cost_usd", Numeric(12, 8), nullable=False), Column("llm_calls", Integer, nullable=False),
    Column("openalex_calls", Integer, nullable=False), Column("tokens_in", BigInteger, nullable=False),
    Column("tokens_out", BigInteger, nullable=False),
    Column("generation", Integer, nullable=False), Column("executions", Integer, nullable=False),
    Column("partial", Boolean, nullable=False), Column("degraded", JSONB, nullable=False),
    Column("error_code", Text), Column("error_step", Text),
    ts("cancel_requested_at"), Column("prompt_versions", JSONB, nullable=False),
    ts("heartbeat_at"), ts("started_at"), ts("finished_at"), ts("created_at", nullable=False), ts("updated_at", nullable=False),
)  # fmt: skip

run_document = Table(
    "run_document", metadata,
    Column("run_id", UUID(as_uuid=True), ForeignKey("analysis_run.id"), primary_key=True),
    Column("document_id", UUID(as_uuid=True), ForeignKey("document.id"), primary_key=True),
    Column("label", Text, nullable=False),
)  # fmt: skip

cluster = Table(
    "cluster", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("analysis_run.id"), nullable=False),
    Column("key", Text, nullable=False), Column("label_key", Text, nullable=False),
    Column("label", Text, nullable=False), Column("description", Text, nullable=False),
    Column("search_terms", JSONB, nullable=False), Column("label_source", Text, nullable=False),
    Column("topic_ids", JSONB, nullable=False), Column("subfield", Text), Column("size", Integer, nullable=False),
    Column("user_works", Integer, nullable=False), Column("insight_weight", Float, nullable=False),
    Column("user_coverage", Float, nullable=False), Column("density", Float, nullable=False),
    Column("candidate_rank", Integer), Column("is_misc", Boolean, nullable=False),
)  # fmt: skip

corpus_work = Table(
    "corpus_work", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("analysis_run.id"), nullable=False),
    Column("openalex_work_id", Text, nullable=False), Column("doi", Text),
    Column("title", Text, nullable=False), Column("abstract", Text), Column("year", Integer),
    Column("cited_by", Integer, nullable=False), Column("type", Text), Column("topics", JSONB, nullable=False),
    Column("primary_topic_id", Text), Column("authors", JSONB, nullable=False), Column("origin", Text, nullable=False),
    Column("query_labels", ARRAY(Text), nullable=False), Column("cluster_id", UUID(as_uuid=True), ForeignKey("cluster.id")),
)  # fmt: skip

novelty_probe = Table(
    "novelty_probe", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("analysis_run.id"), nullable=False),
    Column("label", Text, nullable=False), Column("cluster_id", UUID(as_uuid=True), ForeignKey("cluster.id")),
    Column("query", Text, nullable=False), Column("from_date", Date, nullable=False),
    Column("count", Integer), Column("status", Text, nullable=False), ts("measured_at"),
)  # fmt: skip

opportunity = Table(
    "opportunity", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), ForeignKey("analysis_run.id"), nullable=False),
    Column("owner_id", UUID(as_uuid=True), ForeignKey("account.id"), nullable=False),
    Column("batch", Integer, nullable=False), Column("ord", Integer, nullable=False),
    Column("kind", Text, nullable=False), Column("title", Text, nullable=False), Column("one_line", Text, nullable=False),
    Column("status", Text, nullable=False), Column("status_reason", Text), Column("status_step", Text),
    Column("duplicate_of", UUID(as_uuid=True)), Column("cluster_ids", ARRAY(UUID(as_uuid=True)), nullable=False),
    Column("fit", Numeric(4, 3)), Column("novelty", Numeric(4, 3)), Column("readiness", Numeric(4, 3)),
    Column("effort", Numeric(4, 3)), Column("rank_score", Numeric(4, 3)),
    Column("score_basis", JSONB, nullable=False), Column("novelty_probe_id", UUID(as_uuid=True), ForeignKey("novelty_probe.id")),
    Column("readiness_checklist", JSONB, nullable=False), Column("effort_estimate", Text),
    Column("next_steps", JSONB, nullable=False), Column("analyst_version", Text), Column("critic_version", Text),
    Column("model", Text), Column("critic", JSONB), Column("verify_state", Text, nullable=False),
    ts("created_at", nullable=False), ts("updated_at", nullable=False),
)  # fmt: skip

claim = Table(
    "claim", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("opportunity_id", UUID(as_uuid=True), ForeignKey("opportunity.id"), nullable=False),
    Column("ord", Integer, nullable=False), Column("section", Text, nullable=False), Column("text", Text, nullable=False),
    Column("status", Text, nullable=False), Column("status_reason", Text), Column("set_by", Text, nullable=False),
    Column("agent", Text), Column("prompt_version", Text), Column("model", Text),
    Column("critic_verdict", Text), Column("critic_reason", Text), Column("critic_fix", Text),
    ts("created_at", nullable=False), ts("updated_at", nullable=False),
)  # fmt: skip

evidence = Table(
    "evidence", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("claim_id", UUID(as_uuid=True), ForeignKey("claim.id"), nullable=False),
    Column("ord", Integer, nullable=False), Column("source_type", Text, nullable=False),
    Column("label", Text, nullable=False), Column("ref", Text, nullable=False), Column("quote", Text),
    Column("verified", Boolean), Column("check_code", Text), Column("check_detail", Text),
    Column("corpus_work_id", UUID(as_uuid=True), ForeignKey("corpus_work.id")),
    Column("chunk_id", UUID(as_uuid=True), ForeignKey("document_chunk.id")),
    Column("document_id", UUID(as_uuid=True)), Column("probe_id", UUID(as_uuid=True), ForeignKey("novelty_probe.id")),
    Column("insight_ref", Text),
)  # fmt: skip

feedback = Table(
    "feedback", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("author_id", UUID(as_uuid=True), ForeignKey("account.id"), nullable=False),
    Column("target_type", Text, nullable=False), Column("target_id", UUID(as_uuid=True), nullable=False),
    Column("rating", Text), Column("stars", Integer), Column("reasons", ARRAY(Text), nullable=False),
    Column("comment", Text), ts("created_at", nullable=False), ts("updated_at", nullable=False),
)  # fmt: skip

match_proposal = Table(
    "match_proposal", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("a_id", UUID(as_uuid=True), ForeignKey("account.id")),
    Column("b_id", UUID(as_uuid=True), ForeignKey("account.id")),
    Column("field", Text, nullable=False), Column("score", Float, nullable=False),
    Column("score_basis", JSONB, nullable=False), Column("rationale", JSONB, nullable=False),
    Column("a_public", JSONB, nullable=False), Column("b_public", JSONB, nullable=False),
    Column("state", Text, nullable=False), Column("a_decision", Text), Column("b_decision", Text),
    ts("a_decided_at"), ts("b_decided_at"), Column("declined_by", Text), Column("close_reason", Text),
    Column("never_similar_by", Text), ts("created_at", nullable=False), ts("expires_at", nullable=False),
    ts("revealed_at"), ts("closed_at"), Column("ideas_status", Text, nullable=False),
    Column("ideas_prompt_version", Text), ts("updated_at", nullable=False),
)  # fmt: skip

joint_idea = Table(
    "joint_idea", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True), Column("match_id", UUID(as_uuid=True), nullable=False),
    Column("match_state", Text, nullable=False), Column("ord", Integer, nullable=False),
    Column("title", Text, nullable=False), Column("question", Text, nullable=False),
    Column("uses_from_a", JSONB, nullable=False), Column("uses_from_b", JSONB, nullable=False),
    Column("first_step", Text, nullable=False), Column("possible_output", Text, nullable=False),
    Column("prompt_version", Text), Column("model", Text), ts("created_at", nullable=False),
)  # fmt: skip

match_block = Table(
    "match_block", metadata,
    Column("account_id", UUID(as_uuid=True), ForeignKey("account.id"), primary_key=True),
    Column("blocked_account_id", UUID(as_uuid=True), ForeignKey("account.id"), primary_key=True),
    Column("match_id", UUID(as_uuid=True)), ts("created_at", nullable=False),
)  # fmt: skip

match_profile = Table(
    "match_profile", metadata,
    Column("account_id", UUID(as_uuid=True), ForeignKey("account.id"), primary_key=True),
    Column("researcher_id", UUID(as_uuid=True), ForeignKey("researcher.id"), nullable=False),
    Column("field", Text, nullable=False), Column("region", Text, nullable=False),
    Column("career_band", Text, nullable=False), Column("activity", Float, nullable=False),
    Column("eligible", Boolean, nullable=False), Column("topics_hash", Text, nullable=False),
    Column("methods_hash", Text, nullable=False), Column("vocabulary_version", Text, nullable=False),
    Column("methods_prompt_version", Text, nullable=False), Column("uses_vault_signals", Boolean, nullable=False),
    ts("computed_at", nullable=False), ts("created_at", nullable=False), ts("updated_at", nullable=False),
)  # fmt: skip

match_profile_topic = Table(
    "match_profile_topic", metadata,
    Column("account_id", UUID(as_uuid=True), ForeignKey("match_profile.account_id"), primary_key=True),
    Column("topic_id", Text, primary_key=True), Column("name", Text, nullable=False),
    Column("weight", Float, nullable=False),
)  # fmt: skip

match_profile_method = Table(
    "match_profile_method", metadata,
    Column("account_id", UUID(as_uuid=True), ForeignKey("match_profile.account_id"), primary_key=True),
    Column("method_id", Text, primary_key=True), Column("weight", Float, nullable=False),
    Column("source", Text, primary_key=True),
)  # fmt: skip

match_method_proposal = Table(
    "match_method_proposal", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("account_id", UUID(as_uuid=True), ForeignKey("account.id"), nullable=False),
    Column("label", Text, nullable=False), Column("source_refs", ARRAY(Text), nullable=False), ts("created_at", nullable=False),
)  # fmt: skip
