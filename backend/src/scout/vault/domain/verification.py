"""Deterministic checks on LLM insights: chunk references, verbatim quotes, personal data."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from scout.vault.domain.chunking import Chunk
from scout.vault.domain.insights import (
    DocumentInsights,
    InsightItem,
    ItemStatus,
    Section,
    Stage,
    UnverifiedReason,
)

MIN_QUOTE_CHARS = 15
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_DASHES = dict.fromkeys([*range(0x2010, 0x2016), 0x2212], "-")
_QUOTES = dict.fromkeys([0x2018, 0x2019, 0x201A, 0x201B], "'") | dict.fromkeys(
    [0x201C, 0x201D, 0x201E, 0x201F], '"'
)


def norm(text: str) -> str:
    """Normalise for verbatim matching: NFKC, straight quotes, plain hyphens, joined hyphenation, single spaces."""
    t = unicodedata.normalize("NFKC", text).translate(_DASHES).translate(_QUOTES).replace("­", "")
    t = re.sub(r"-\s+", "-", t)
    return re.sub(r"\s+", " ", t).strip()


def _strings(data: Mapping[str, Any]) -> list[str]:
    return [v for v in data.values() if isinstance(v, str)]


def _item(
    section: Section, data: dict[str, Any], labels: list[str], by_label: Mapping[str, Chunk], stage: Stage
) -> InsightItem:
    reason: UnverifiedReason | None = None
    cited = [by_label[x] for x in labels if x in by_label]
    if not labels:
        reason = UnverifiedReason.NO_CHUNK_REF
    elif len(cited) != len(labels):
        reason = UnverifiedReason.UNKNOWN_CHUNK
    elif any(_EMAIL.search(s) for s in _strings(data)):
        reason = UnverifiedReason.PERSONAL_DATA
    elif section is Section.REVIEWER_OBJECTIONS:
        quote = norm(str(data.get("quote", "")))
        haystack = norm(" ".join(c.text for c in sorted(cited, key=lambda c: c.ord)))
        if len(quote) < MIN_QUOTE_CHARS:
            reason = UnverifiedReason.QUOTE_TOO_SHORT
        elif quote not in haystack:
            reason = UnverifiedReason.QUOTE_NOT_FOUND
    return InsightItem(
        section=section, data=data, chunk_labels=tuple(labels), chunk_ids=tuple(c.id for c in cited),
        status=ItemStatus.VERIFIED if reason is None else ItemStatus.UNVERIFIED, reason=reason, stage=stage,
    )  # fmt: skip


def verify(raw: DocumentInsights, chunks_by_label: Mapping[str, Chunk], stage: Stage) -> list[InsightItem]:
    """Turn raw LLM output into stored items, marking every item verified or unverified with a reason."""
    items: list[InsightItem] = []
    for section in (Section.AIMS, Section.FINDINGS, Section.OPEN_QUESTIONS):
        for it in getattr(raw, section.value):
            items.append(_item(section, {"summary": it.summary}, it.chunk_ids, chunks_by_label, stage))
    for m in raw.methods:
        items.append(
            _item(Section.METHODS, {"name": m.name, "detail": m.detail}, m.chunk_ids, chunks_by_label, stage)
        )
    for d in raw.datasets:
        items.append(
            _item(Section.DATASETS, {"name": d.name, "status": d.status}, d.chunk_ids, chunks_by_label, stage)
        )
    for o in raw.reviewer_objections:
        data = {"quote": o.quote, "summary": o.summary}
        items.append(_item(Section.REVIEWER_OBJECTIONS, data, o.chunk_ids, chunks_by_label, stage))
    return items
