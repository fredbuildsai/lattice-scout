"""Map-reduce slicing for long documents."""

from __future__ import annotations

from collections.abc import Sequence

from scout.vault.domain.chunking import Chunk


def plan_slices(
    chunks: Sequence[Chunk], full_max: int = 120_000, slice_chars: int = 60_000
) -> list[list[Chunk]]:
    """One slice when the text fits ``full_max``; otherwise greedy consecutive slices of about ``slice_chars``.

    A chunk is never split across slices.
    """
    total = sum(len(c.text) for c in chunks)
    if total <= full_max:
        return [list(chunks)] if chunks else []
    slices: list[list[Chunk]] = []
    current: list[Chunk] = []
    size = 0
    for c in chunks:
        if current and size + len(c.text) > slice_chars:
            slices.append(current)
            current, size = [], 0
        current.append(c)
        size += len(c.text)
    if current:
        slices.append(current)
    return slices
