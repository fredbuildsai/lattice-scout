"""Insight extraction port (LLM-backed)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from scout.ports.llm import CallContext
from scout.vault.domain.enums import DocKind
from scout.vault.domain.insights import DocumentInsights


@dataclass(frozen=True)
class ChunkInput:
    """A chunk as the model sees it: ``chunk_id`` is the label (``c12``)."""

    chunk_id: str
    page: int
    text: str


@dataclass(frozen=True)
class InsightRequest:
    """One call: ``full`` and ``map`` carry chunks, ``reduce`` carries partial results."""

    mode: Literal["full", "map", "reduce"]
    filename: str
    kind_hint: DocKind | None
    chunks: tuple[ChunkInput, ...] = ()
    partials: tuple[DocumentInsights, ...] = ()


@dataclass(frozen=True)
class InsightOutcome:
    """Model output and its accounting."""

    insights: DocumentInsights
    prompt_version: str
    model: str
    cost_usd: float


class InsightExtractorPort(Protocol):
    """Extract structured insights from document text."""

    def confidential_allowed(self) -> bool:
        """Whether document text may be sent to the model at all (an authorized endpoint exists, ADR 016)."""
        ...

    async def extract(self, req: InsightRequest, ctx: CallContext) -> InsightOutcome:
        """Run the agent.

        Raises:
            LLMUnavailable: transient; the job retries.
            LLMOutputInvalid: transient; the job retries.
        """
        ...
