"""``ExtractInsights``: one call for short documents, map-reduce for long ones, then deterministic verification."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from scout.ports.llm import CallContext
from scout.vault.domain.chunking import Chunk
from scout.vault.domain.document import Document
from scout.vault.domain.insights import DocumentInsights, InsightItem, ItemStatus, Stage, StoredInsights
from scout.vault.domain.slicing import plan_slices
from scout.vault.domain.verification import EMAIL, verify
from scout.vault.ports.insights import ChunkInput, InsightExtractorPort, InsightOutcome, InsightRequest

_SECTIONS = ("aims", "findings", "open_questions", "methods", "datasets", "reviewer_objections")


def _only_verified(raw: DocumentInsights, items: Sequence[InsightItem]) -> DocumentInsights:
    """``raw`` without the items ``verify`` rejected (``verify`` yields items in section order, list by list)."""
    flags = iter(i.status is ItemStatus.VERIFIED for i in items)
    kept = {name: [x for x in getattr(raw, name) if next(flags)] for name in _SECTIONS}
    return raw.model_copy(update=kept)


class ExtractInsights:
    """Run the ``InsightExtractor`` over a document's chunks."""

    def __init__(
        self,
        extractor: InsightExtractorPort,
        full_max: int = 120_000,
        slice_chars: int = 60_000,
        concurrency: int = 3,
    ) -> None:
        self._x, self._full_max, self._slice_chars, self._concurrency = (
            extractor,
            full_max,
            slice_chars,
            concurrency,
        )

    def available(self) -> bool:
        """Whether insights can be extracted now (document text needs an authorized endpoint)."""
        return self._x.confidential_allowed()

    @staticmethod
    def _inputs(chunks: Sequence[Chunk]) -> tuple[ChunkInput, ...]:
        return tuple(ChunkInput(c.label, c.page_from, c.text) for c in chunks)

    async def run(self, doc: Document, chunks: Sequence[Chunk], ctx: CallContext) -> StoredInsights:
        """Extract and verify insights.

        Raises:
            LLMUnavailable: transient provider failure.
            LLMOutputInvalid: the model's output did not match the schema.
        """
        slices = plan_slices(chunks, self._full_max, self._slice_chars)
        by_label = {c.label: c for c in chunks}
        if len(slices) <= 1:
            out = await self._x.extract(
                InsightRequest("full", doc.filename, doc.kind, self._inputs(chunks)), ctx
            )
            return self._stored(doc, out, verify(out.insights, by_label, Stage.FULL), "full", 1, out.cost_usd)

        gate = asyncio.Semaphore(self._concurrency)

        async def one(part: Sequence[Chunk]) -> InsightOutcome:
            async with gate:
                return await self._x.extract(
                    InsightRequest("map", doc.filename, doc.kind, self._inputs(part)), ctx
                )

        outs = await asyncio.gather(*(one(s) for s in slices))
        partials: list[DocumentInsights] = []
        unverified: list[InsightItem] = []
        for part, out in zip(slices, outs, strict=True):
            items = verify(out.insights, {c.label: c for c in part}, Stage.MAP)
            partials.append(_only_verified(out.insights, items))
            unverified += [i for i in items if i.status is ItemStatus.UNVERIFIED]
        merged = await self._x.extract(
            InsightRequest("reduce", doc.filename, doc.kind, (), tuple(partials)), ctx
        )
        items = verify(merged.insights, by_label, Stage.REDUCE) + unverified
        cost = sum(o.cost_usd for o in outs) + merged.cost_usd
        return self._stored(doc, merged, items, "map_reduce", len(slices), cost)

    @staticmethod
    def _stored(
        doc: Document, out: InsightOutcome, items: list[InsightItem], mode: str, slices: int, cost: float
    ) -> StoredInsights:
        raw = out.insights
        title = None if raw.title and EMAIL.search(raw.title) else raw.title
        keywords = [k for k in raw.keywords if not EMAIL.search(k)]
        return StoredInsights(
            document_id=doc.id, items=items, keywords=keywords, title=title, kind_suggested=raw.kind,
            kind_reason=raw.kind_reason, prompt_version=out.prompt_version, model=out.model,
            mode="map_reduce" if mode == "map_reduce" else "full", slices=slices, cost_usd=cost,
        )  # fmt: skip
