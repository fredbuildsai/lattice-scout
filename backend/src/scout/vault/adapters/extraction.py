"""``PdfDocxExtractor``: pypdf / python-docx (pdfplumber fallback) in a resource-limited child process."""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import sys

from scout.vault.domain.enums import DocumentMime, PageBasis
from scout.vault.ports.extraction import ExtractedText, ExtractionRejected, RejectCode

_CODES = {
    "encrypted", "no_text_layer", "too_many_pages", "text_too_long", "corrupt_file", "zip_bomb",
    "extraction_timeout", "too_complex",
}  # fmt: skip


class PdfDocxExtractor:
    """Implements ``TextExtractionPort``; parsing never runs in the worker process itself."""

    def __init__(
        self, *, timeout_s: int = 60, memory_mb: int = 1024, max_pages: int = 300, max_chars: int = 720_000
    ) -> None:
        self._timeout, self._memory, self._max_pages, self._max_chars = (
            timeout_s,
            memory_mb,
            max_pages,
            max_chars,
        )

    async def extract_text(self, data: bytes, mime: DocumentMime) -> ExtractedText:
        """Run the child, map its answer; ``ExtractionRejected`` for every permanent failure."""
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-m", "scout.vault.adapters.extract_child",
            mime.value, str(self._max_pages), str(self._max_chars), str(self._memory), str(self._timeout),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        try:
            out, _ = await asyncio.wait_for(proc.communicate(data), timeout=self._timeout + 5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise ExtractionRejected("extraction_timeout") from None
        if proc.returncode == -signal.SIGXCPU:
            raise ExtractionRejected("extraction_timeout")
        if proc.returncode != 0:
            raise ExtractionRejected("corrupt_file")
        try:
            result = json.loads(out)
        except ValueError:
            raise ExtractionRejected("corrupt_file") from None
        if "error" in result:
            code = result["error"]
            raise ExtractionRejected(code if code in _CODES else "corrupt_file")
        return ExtractedText(
            pages=tuple(result["pages"]), page_basis=PageBasis(result["basis"]), extractor=result["extractor"]
        )


__all__ = ["PdfDocxExtractor", "RejectCode"]
