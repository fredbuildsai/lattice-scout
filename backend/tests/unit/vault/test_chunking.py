"""Chunking invariants."""

import re
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st

from scout.vault.domain.chunking import ChunkParams, chunk_pages
from scout.vault.domain.slicing import plan_slices

DOC = UUID(int=5)
P = ChunkParams(target=200, max=300, min=60)
words = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), max_codepoint=0x24F), min_size=1, max_size=12
)
sentence = st.lists(words, min_size=1, max_size=15).map(lambda w: " ".join(w) + ".")
paragraph = st.lists(sentence, min_size=1, max_size=12).map(" ".join)
page = st.lists(paragraph, min_size=0, max_size=4).map("\n\n".join)


def squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


@settings(max_examples=80, deadline=None)
@given(st.lists(page, min_size=1, max_size=8))
def test_invariants(pages: list[str]) -> None:
    chunks = chunk_pages(DOC, pages, P)
    assert [c.ord for c in chunks] == list(range(len(chunks)))
    assert all(0 < len(c.text) <= P.max for c in chunks)
    assert all(1 <= c.page_from <= c.page_to <= len(pages) for c in chunks)
    froms = [c.page_from for c in chunks]
    tos = [c.page_to for c in chunks]
    assert froms == sorted(froms) and tos == sorted(tos)
    assert squash("".join(c.text for c in chunks)) == squash("".join(pages))
    assert [c.id for c in chunks] == [c.id for c in chunk_pages(DOC, pages, P)]  # deterministic


def test_page_offsets_and_labels() -> None:
    pages = [
        "Intro paragraph.\n\nMore intro.",
        "Methods here.",
        "Reviewer 2: not convinced about fire safety.",
    ]
    chunks = chunk_pages(DOC, pages, ChunkParams(target=40, max=80, min=5))
    assert chunks[0].label == "c0" and chunks[0].page_from == 1
    assert chunks[-1].page_to == 3 and "Reviewer 2" in chunks[-1].text
    assert all(chunks[i].char_end < chunks[i + 1].char_start for i in range(len(chunks) - 1))


def test_long_paragraph_splits_and_control_chars_go() -> None:
    para = "word " * 500
    chunks = chunk_pages(DOC, ["a\x00b\r\n" + para], P)
    assert len(chunks) > 5 and all(len(c.text) <= P.max for c in chunks)
    assert "\x00" not in chunks[0].text and chunks[0].text.startswith("ab")


def test_short_tail_merges() -> None:
    chunks = chunk_pages(DOC, ["A" * 180 + "\n\n" + "B" * 70 + "\n\n" + "C" * 10], P)
    assert chunks[-1].text.endswith("C" * 10) and len(chunks) == 2


def test_empty() -> None:
    assert chunk_pages(DOC, ["", "   "]) == []


def test_slices() -> None:
    chunks = chunk_pages(DOC, ["x" * 90 + "\n\n" for _ in range(1)] + ["word " * 800] * 20, ChunkParams())
    assert len(plan_slices(chunks, full_max=10**9)) == 1
    slices = plan_slices(chunks, full_max=1000, slice_chars=10_000)
    assert len(slices) > 1
    assert [c.ord for s in slices for c in s] == [c.ord for c in chunks]  # order kept, nothing split or lost
    assert all(sum(len(c.text) for c in s) <= 10_000 + 4000 for s in slices)
    assert plan_slices([]) == []
