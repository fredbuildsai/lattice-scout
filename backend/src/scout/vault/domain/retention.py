"""What survives of a chunk when the ``discard`` policy removes the original text."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from scout.vault.domain.chunking import Chunk
from scout.vault.domain.ids import ChunkId
from scout.vault.domain.insights import InsightItem, ItemStatus, Section
from scout.vault.domain.verification import norm

MAX_QUOTE = 300
SUMMARY_SEP = " · "


@dataclass(frozen=True)
class Quote:
    """A verbatim passage kept as evidence (at most 300 characters)."""

    text: str
    truncated: bool


@dataclass(frozen=True)
class Retained:
    """The only content a discarded chunk keeps."""

    quotes: tuple[Quote, ...]
    summary: str | None


def _cut(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > 0 else limit].rstrip(), True


def retained_content(chunks: Sequence[Chunk], items: Sequence[InsightItem]) -> dict[ChunkId, Retained]:
    """Per chunk: verified objection quotes (attached to the first cited chunk) and a joined summary.

    Chunks no verified item cites keep nothing but ids, offsets and pages.
    """
    order = {c.id: c.ord for c in chunks}
    quotes: dict[ChunkId, list[Quote]] = {}
    summaries: dict[ChunkId, list[str]] = {}
    for it in items:
        if it.status is not ItemStatus.VERIFIED:
            continue
        cited = sorted((cid for cid in it.chunk_ids if cid in order), key=lambda cid: order[cid])
        if not cited:
            continue
        if it.section is Section.REVIEWER_OBJECTIONS:
            text, truncated = _cut(norm(str(it.data["quote"])), MAX_QUOTE)
            quotes.setdefault(cited[0], []).append(Quote(text, truncated))
        for cid in cited:
            if it.summary_text:
                summaries.setdefault(cid, []).append(it.summary_text)
    out: dict[ChunkId, Retained] = {}
    for cid in set(quotes) | set(summaries):
        joined = SUMMARY_SEP.join(summaries.get(cid, []))
        summary = _cut(joined, MAX_QUOTE)[0] if joined else None
        out[cid] = Retained(tuple(quotes.get(cid, [])), summary)
    return out
