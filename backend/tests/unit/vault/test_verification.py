"""Insight verification and retained content."""

# ruff: noqa: RUF001  (typographic characters are the point of these tests)

from typing import Any
from uuid import UUID

import pytest

from scout.vault.domain.chunking import Chunk
from scout.vault.domain.ids import chunk_id
from scout.vault.domain.insights import (
    Dataset,
    DocumentInsights,
    InsightItem,
    Item,
    ItemStatus,
    Method,
    Objection,
    Section,
    Stage,
    UnverifiedReason,
)  # fmt: skip
from scout.vault.domain.retention import retained_content
from scout.vault.domain.verification import norm, verify

DOC = UUID(int=3)
T41 = "Reviewer 2: The applicants do not demonstrate that the foams meet fire-safety standard EN 13501 at the pro-\nposed bio-content."
T42 = "They also give “no evidence” of long term durability – a serious concern."


def chunk(ord_: int, text: str) -> Chunk:
    return Chunk(chunk_id(DOC, ord_), DOC, ord_, ord_ + 1, ord_ + 1, 0, len(text), text)


CHUNKS = {c.label: c for c in (chunk(0, "Aims text."), chunk(41, T41), chunk(42, T42))}


def one(**kw: Any) -> list[InsightItem]:
    return verify(DocumentInsights(**kw), CHUNKS, Stage.FULL)


def test_norm() -> None:
    assert norm("a–b  “q”­ x") == 'a-b "q" x'
    assert norm("pro-\n  posed") == "pro-posed"


def test_verbatim_quote_with_hyphenation_and_whitespace() -> None:
    q = "The applicants do not demonstrate that the foams meet fire-safety standard EN 13501 at the pro-posed bio-content."
    [item] = one(reviewer_objections=[Objection(quote=q, summary="s", chunk_ids=["c41"])])
    assert item.status is ItemStatus.VERIFIED and item.chunk_ids == (chunk_id(DOC, 41),)


def test_quote_across_two_chunks_and_typography() -> None:
    q = 'They also give "no evidence" of long term durability - a serious concern.'
    [ok] = one(reviewer_objections=[Objection(quote=q, summary="s", chunk_ids=["c42"])])
    assert ok.status is ItemStatus.VERIFIED
    cross = "standard EN 13501 at the pro-posed bio-content. They also give"
    [ok2] = one(reviewer_objections=[Objection(quote=cross, summary="s", chunk_ids=["c41", "c42"])])
    assert ok2.status is ItemStatus.VERIFIED


@pytest.mark.parametrize(
    ("labels", "quote", "reason"),
    [
        ([], "The applicants do not demonstrate that", UnverifiedReason.NO_CHUNK_REF),
        (["c99"], "The applicants do not demonstrate that", UnverifiedReason.UNKNOWN_CHUNK),
        (["c41", "c99"], "The applicants do not demonstrate that", UnverifiedReason.UNKNOWN_CHUNK),
        (["c41"], "too short", UnverifiedReason.QUOTE_TOO_SHORT),
        (
            ["c41"],
            "The applicants clearly demonstrate that everything works",
            UnverifiedReason.QUOTE_NOT_FOUND,
        ),
        (["c42"], "The applicants do not demonstrate that", UnverifiedReason.QUOTE_NOT_FOUND),  # wrong chunk
    ],
)
def test_unverified_reasons(labels: list[str], quote: str, reason: UnverifiedReason) -> None:
    [item] = one(reviewer_objections=[Objection(quote=quote, summary="s", chunk_ids=labels)])
    assert item.status is ItemStatus.UNVERIFIED and item.reason is reason


def test_other_sections_and_personal_data() -> None:
    items = one(
        aims=[Item(summary="Make foams", chunk_ids=["c0"]), Item(summary="no ref", chunk_ids=[])],
        findings=[Item(summary="ask jane@example.edu", chunk_ids=["c0"])],
        methods=[Method(name="GC-MS", detail=None, chunk_ids=["c0"])],
        datasets=[Dataset(name="Foam set", status="planned", chunk_ids=["c0"])],
    )
    assert [(i.section, i.status) for i in items] == [
        (Section.AIMS, ItemStatus.VERIFIED), (Section.AIMS, ItemStatus.UNVERIFIED),
        (Section.FINDINGS, ItemStatus.UNVERIFIED), (Section.METHODS, ItemStatus.VERIFIED),
        (Section.DATASETS, ItemStatus.VERIFIED),
    ]  # fmt: skip
    assert items[2].reason is UnverifiedReason.PERSONAL_DATA


def test_retained_content_quotes_and_summaries() -> None:
    long_q = "The applicants do not demonstrate that the foams meet fire-safety standard EN 13501 at the pro-posed bio-content."
    items = one(
        aims=[Item(summary="Make foams", chunk_ids=["c0", "c41"])],
        reviewer_objections=[
            Objection(quote=long_q, summary="fire", chunk_ids=["c41"]),
            Objection(quote="fabricated quote that is not in the text", summary="x", chunk_ids=["c41"]),
        ],
    )
    kept = retained_content(list(CHUNKS.values()), items)
    assert set(kept) == {chunk_id(DOC, 0), chunk_id(DOC, 41)}  # c42 is cited by nobody: nothing kept
    r41 = kept[chunk_id(DOC, 41)]
    assert [q.text for q in r41.quotes] == [norm(long_q)]  # the fabricated (unverified) quote is dropped
    assert r41.summary is not None and "Make foams" in r41.summary and "fire" in r41.summary
    assert kept[chunk_id(DOC, 0)].quotes == ()


def test_quote_truncated_at_word_boundary() -> None:
    text = ("lorem ipsum " * 60).strip()
    c = chunk(7, text)
    obj = Objection(quote=text, summary="s", chunk_ids=["c7"])
    items = verify(DocumentInsights(reviewer_objections=[obj]), {"c7": c}, Stage.FULL)
    [q] = retained_content([c], items)[c.id].quotes
    assert q.truncated and len(q.text) <= 300 and text.startswith(q.text) and not q.text.endswith(" ")
