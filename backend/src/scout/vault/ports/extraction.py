"""Text extraction port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from scout.shared import DomainError
from scout.vault.domain.enums import DocumentMime, PageBasis

RejectCode = Literal[
    "encrypted", "no_text_layer", "too_many_pages", "text_too_long", "corrupt_file", "zip_bomb",
    "extraction_timeout", "too_complex",
]  # fmt: skip


class ExtractionRejected(DomainError):
    """The file can never be processed (permanent: the job does not retry)."""

    code = "extraction_rejected"
    http_status = 422
    title = "Document cannot be processed"

    def __init__(self, reject_code: RejectCode) -> None:
        super().__init__(reject_code, extra={"reason": reject_code})
        self.reject_code: RejectCode = reject_code


@dataclass(frozen=True)
class ExtractedText:
    """Page texts (index 0 = page 1) and how trustworthy the page numbers are."""

    pages: tuple[str, ...]
    page_basis: PageBasis
    extractor: str


class TextExtractionPort(Protocol):
    """Turn a PDF or DOCX into page texts."""

    async def extract_text(self, data: bytes, mime: DocumentMime) -> ExtractedText:
        """Extract text.

        Raises:
            ExtractionRejected: for permanent problems (encrypted, scanned, too large, corrupt, timeout).
        """
        ...
