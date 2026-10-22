"""Pure guards on model text: keep one person's identity out of what the other sees, keep vault text out of ideas."""

from __future__ import annotations

import re
from collections.abc import Iterable

from scout.shared.text import norm

_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_PATTERNS = (
    _YEAR,
    re.compile(r"\b10\.\d{4,9}/\S+"),  # DOI
    re.compile(r"https?://|www\.", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    re.compile(r"\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b"),  # ORCID
    re.compile(r"\bW\d{5,}\b"),  # OpenAlex work id
)


def _words(text: str) -> list[str]:
    return norm(text).split()


def shingles(text: str, n: int) -> set[tuple[str, ...]]:
    """All runs of ``n`` consecutive normalised words."""
    w = _words(text)
    return {tuple(w[i : i + n]) for i in range(len(w) - n + 1)}


def leaks_identity(text: str, titles: Iterable[str]) -> bool:
    """True when ``text`` carries a year, DOI, URL, e-mail, ORCID, OpenAlex id or 6 words of an input title."""
    if any(p.search(text) for p in _PATTERNS):
        return True
    mine = shingles(text, 6)
    return any(mine & shingles(t, 6) for t in titles)


def copies_signal(text: str, signals: Iterable[str]) -> bool:
    """True when ``text`` repeats 8 consecutive words of a vault signal."""
    mine = shingles(text, 8)
    return any(mine & shingles(s, 8) for s in signals)


_TITLED_NAME = re.compile(
    r"\b(?:Dr|Prof|Professor|Mr|Mrs|Ms|Mx)\b\.?\s+[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?"
)  # "Dr. Jane Smith", "Prof Lee"
_ET_AL = re.compile(r"\b[A-Z][\w'-]+\s+et\s+al\.?")


def clean_signal(text: str, max_len: int = 300) -> str:
    """Strip e-mails, URLs, phone numbers and titled third-party names from a signal and bound it."""
    out = _TITLED_NAME.sub("a colleague", text)
    out = _ET_AL.sub("a published study", out)
    out = re.sub(r"https?://\S+|www\.\S+", "", out)
    out = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "", out)
    out = re.sub(r"\+?\d[\d\s().-]{7,}\d", "", out)
    return " ".join(out.split())[:max_len]
