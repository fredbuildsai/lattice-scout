"""SQLAlchemy Core table definitions for the context repositories (schema owned by Alembic migrations)."""

from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
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
