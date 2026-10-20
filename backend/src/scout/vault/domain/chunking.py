"""Deterministic chunking with page offsets."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from scout.vault.domain.ids import ChunkId, chunk_id

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANKS = re.compile(r"\n{3,}")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ChunkParams:
    """Size limits in characters."""

    target: int = 3000
    max: int = 4000
    min: int = 600


@dataclass(frozen=True)
class Chunk:
    """A slice of the document text. ``label`` (``c12``) is what the LLM sees and cites."""

    id: ChunkId
    document_id: UUID
    ord: int
    page_from: int
    page_to: int
    char_start: int
    char_end: int
    text: str

    @property
    def label(self) -> str:
        """Short reference used in prompts."""
        return f"c{self.ord}"


def normalise_page(text: str) -> str:
    """NFC, ``\\n`` newlines, no control characters, no trailing spaces, at most one blank line in a row."""
    t = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    t = _CONTROL.sub("", t)
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    return _BLANKS.sub("\n\n", t).strip()


def _split_long(paragraph: str, limit: int) -> list[str]:
    parts: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(paragraph):
        while len(sentence) > limit:  # no sentence end within the limit: cut at whitespace
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                parts.append(current)
                current = ""
            parts.append(sentence[:cut].rstrip())
            sentence = sentence[cut:].lstrip()
        if current and len(current) + 1 + len(sentence) > limit:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip() if current else sentence
    if current:
        parts.append(current)
    return parts


def chunk_pages(document_id: UUID, pages: Sequence[str], params: ChunkParams | None = None) -> list[Chunk]:
    """Split page texts into chunks.

    Paragraphs are packed greedily up to ``target``; longer ones split at sentence ends or whitespace; a short
    tail merges into the previous chunk when it still fits ``max``. A chunk may cross pages.
    """
    p = params or ChunkParams()
    pieces: list[tuple[str, int]] = []  # (paragraph, page)
    for number, raw in enumerate(pages, start=1):
        for para in normalise_page(raw).split("\n\n"):
            para = para.strip()
            if not para:
                continue
            for part in _split_long(para, p.max) if len(para) > p.max else [para]:
                pieces.append((part, number))

    groups: list[list[tuple[str, int]]] = []
    size = 0
    for piece in pieces:
        if groups and size + 2 + len(piece[0]) <= p.target:
            groups[-1].append(piece)
            size += 2 + len(piece[0])
        else:
            groups.append([piece])
            size = len(piece[0])

    def length(g: list[tuple[str, int]]) -> int:
        return sum(len(t) for t, _ in g) + 2 * (len(g) - 1)

    if (
        len(groups) > 1
        and length(groups[-1]) < p.min
        and length(groups[-2]) + 2 + length(groups[-1]) <= p.max
    ):
        groups[-2].extend(groups.pop())

    chunks: list[Chunk] = []
    offset = 0
    for ord_, g in enumerate(groups):
        text = "\n\n".join(t for t, _ in g)
        chunks.append(
            Chunk(
                id=chunk_id(document_id, ord_),
                document_id=document_id,
                ord=ord_,
                page_from=g[0][1],
                page_to=g[-1][1],
                char_start=offset,
                char_end=offset + len(text),
                text=text,
            )  # fmt: skip
        )
        offset += len(text) + 2
    return chunks
