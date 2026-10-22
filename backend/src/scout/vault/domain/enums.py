"""Vault enumerations."""

from __future__ import annotations

from enum import StrEnum


class DocKind(StrEnum):
    """What a document is (ARCHITECTURE §4.2)."""

    FUNDED_GRANT = "funded_grant"
    DECLINED_GRANT = "declined_grant"
    PROPOSAL = "proposal"
    UNPUBLISHED_RESULTS = "unpublished_results"
    RAW_DATA_DESCRIPTION = "raw_data_description"
    LAB_NOTES = "lab_notes"
    OTHER = "other"


class DocumentMime(StrEnum):
    """Accepted file types."""

    PDF = "application/pdf"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class DocStatus(StrEnum):
    """Document lifecycle (DECISIONS 41: ``analysing`` rather than ``analysing_insights``)."""

    UPLOADED = "uploaded"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    ANALYSING = "analysing"
    AWAITING_PROVIDER = (
        "awaiting_approved_provider"  # text is read; insights wait for an authorized LLM endpoint (ADR 016)
    )
    READY = "ready"
    FAILED = "failed"
    REJECTED = "rejected"
    DELETING = "deleting"


class RetentionPolicy(StrEnum):
    """Deployment-wide retention setting (D6, ADR-009)."""

    KEEP = "keep"
    DISCARD = "discard"
    USER_CHOICE = "user_choice"


class Retention(StrEnum):
    """What happens to one document's original bytes and chunk text."""

    KEEP = "keep"
    DISCARD = "discard"


class PageBasis(StrEnum):
    """How reliable page numbers are."""

    PHYSICAL = "physical"
    PAGE_BREAKS = "page_breaks"
    NONE = "none"
