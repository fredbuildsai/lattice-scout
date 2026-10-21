"""Child process that parses one untrusted document (so a hostile file cannot take down the worker).

Usage: ``python -m scout.vault.adapters.extract_child <mime> <max_pages> <max_chars> <memory_mb> <cpu_s>`` with
the file bytes on stdin; one JSON line on stdout: ``{"pages": [...], "basis": "...", "extractor": "..."}`` or
``{"error": "<reject code>"}``. Never logs document text.
"""

from __future__ import annotations

import io
import json
import resource
import sys
from typing import Any

MIN_TEXT_CHARS = 200
PDF = "application/pdf"


class Reject(Exception):
    """Permanent rejection with a code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _pdf_pypdf(data: bytes, max_pages: int) -> list[str]:
    import pypdf
    from pypdf.errors import PyPdfError

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise Reject("encrypted")
        if len(reader.pages) > max_pages:
            raise Reject("too_many_pages")
        return [(page.extract_text() or "") for page in reader.pages]
    except PyPdfError as exc:
        raise ValueError("pypdf failed") from exc


def _pdf_plumber(data: bytes, max_pages: int) -> list[str]:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if len(pdf.pages) > max_pages:
            raise Reject("too_many_pages")
        return [(page.extract_text() or "") for page in pdf.pages]


def extract_pdf(data: bytes, max_pages: int) -> tuple[list[str], str, str]:
    """pypdf first; pdfplumber when pypdf fails or finds (almost) no text."""
    pages: list[str] | None = None
    try:
        pages = _pdf_pypdf(data, max_pages)
        if sum(len(p.strip()) for p in pages) >= MIN_TEXT_CHARS:
            return pages, "physical", "pypdf"
    except Reject:
        raise
    except Exception:
        pages = None
    try:
        fallback = _pdf_plumber(data, max_pages)
    except Reject:
        raise
    except Exception:
        if pages is None:
            raise Reject("corrupt_file") from None
        fallback = []
    best = (
        fallback
        if sum(len(p.strip()) for p in fallback) >= sum(len(p.strip()) for p in pages or [])
        else pages
    )
    return best or [], "physical", "pdfplumber"


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx(data: bytes, max_pages: int) -> tuple[list[str], str, str]:
    """Pages split at explicit page breaks and Word's last-rendered markers; tables become ``a | b`` rows."""
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception:
        raise Reject("corrupt_file") from None
    pages: list[list[str]] = [[]]
    breaks = 0

    def new_page() -> None:
        nonlocal breaks
        if pages[-1]:  # an empty page is not started twice (explicit and rendered markers coincide)
            pages.append([])
            breaks += 1

    def paragraph(p: Any) -> str:
        out: list[str] = []
        for el in p.iter():
            if el.tag == f"{_W}t":
                out.append(el.text or "")
            elif el.tag == f"{_W}tab":
                out.append("\t")
            elif (
                el.tag == f"{_W}br" and el.get(f"{_W}type") == "page"
            ) or el.tag == f"{_W}lastRenderedPageBreak":
                if "".join(out).strip():
                    pages[-1].append("".join(out))
                    out = []
                new_page()
            elif el.tag == f"{_W}br":
                out.append("\n")
        return "".join(out)

    for child in document.element.body.iterchildren():
        if child.tag == f"{_W}p":
            text = paragraph(child)
            if text.strip():
                pages[-1].append(text)
        elif child.tag == f"{_W}tbl":
            for row in child.iter(f"{_W}tr"):
                cells = []
                for cell in row.iter(f"{_W}tc"):
                    cells.append(
                        " ".join(t for t in (paragraph(p).strip() for p in cell.iter(f"{_W}p")) if t)
                    )
                line = " | ".join(c for c in cells if c)
                if line:
                    pages[-1].append(line)
    texts = ["\n\n".join(p) for p in pages if p]
    if len(texts) > max_pages:
        raise Reject("too_many_pages")
    return texts or [""], ("page_breaks" if breaks else "none"), "python-docx"


def main(argv: list[str]) -> int:
    """Entry point."""
    mime, max_pages, max_chars, memory_mb, cpu_s = (
        argv[0],
        int(argv[1]),
        int(argv[2]),
        int(argv[3]),
        int(argv[4]),
    )
    resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 1))
    data = sys.stdin.buffer.read()
    try:
        pages, basis, extractor = (extract_pdf if mime == PDF else extract_docx)(data, max_pages)
        total = sum(len(p) for p in pages)
        if total > max_chars:
            raise Reject("text_too_long")
        if total < MIN_TEXT_CHARS and not (mime != PDF and total > 0):
            raise Reject("no_text_layer")
        out: dict[str, Any] = {"pages": pages, "basis": basis, "extractor": extractor}
    except Reject as r:
        out = {"error": r.code}
    except MemoryError:
        out = {"error": "too_complex"}
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
