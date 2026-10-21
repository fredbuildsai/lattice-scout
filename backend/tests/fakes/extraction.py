"""``FakeTextExtractor``: bytes are UTF-8 text, form feed (``\\f``) separates pages."""

from __future__ import annotations

from scout.vault.domain.enums import DocumentMime, PageBasis
from scout.vault.ports.extraction import ExtractedText, ExtractionRejected, RejectCode


class FakeTextExtractor:
    """Deterministic extractor for use-case tests."""

    def __init__(self) -> None:
        self.calls = 0
        self._reject: RejectCode | None = None
        self._fail_next = 0

    def script_reject(self, code: RejectCode | None) -> None:
        """Make every call raise ``ExtractionRejected(code)`` (``None`` turns it off)."""
        self._reject = code

    def fail_next(self, n: int = 1) -> None:
        """Raise a transient ``RuntimeError`` for the next ``n`` calls."""
        self._fail_next = n

    async def extract_text(self, data: bytes, mime: DocumentMime) -> ExtractedText:
        self.calls += 1
        if self._fail_next > 0:
            self._fail_next -= 1
            raise RuntimeError("extractor crashed")
        if self._reject:
            raise ExtractionRejected(self._reject)
        pages = tuple(data.decode("utf-8").split("\f"))
        return ExtractedText(pages=pages, page_basis=PageBasis.PHYSICAL, extractor="fake@1")
