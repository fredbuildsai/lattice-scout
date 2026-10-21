"""``InsightExtractorPort`` over the ``InsightExtractor`` agent."""

from __future__ import annotations

from pydantic import BaseModel

from scout.adapters.llm.factory import AgentFactory
from scout.ports.llm import CallContext, ModelTier
from scout.shared.confidential import AgentInput, confidential_field
from scout.vault.domain.insights import DocumentInsights
from scout.vault.ports.insights import InsightOutcome, InsightRequest

AGENT = "InsightExtractor"


class _Chunk(BaseModel):
    chunk_id: str
    page: int
    text: str


class _Input(AgentInput):
    """Wholly confidential: document text has no published form."""

    mode: str = confidential_field()
    filename: str = confidential_field()
    kind_hint: str | None = confidential_field()
    chunks: list[_Chunk] = confidential_field()
    partials: list[DocumentInsights] = confidential_field()


class AgentsInsightExtractor:
    """Sends the request as JSON to the ``InsightExtractor`` agent (tier ``fast``)."""

    def __init__(self, factory: AgentFactory) -> None:
        self._factory = factory
        self._spec = factory.spec(AGENT, DocumentInsights)  # a broken prompt fails at startup

    def confidential_allowed(self) -> bool:
        """Whether an authorized endpoint exists for document text."""
        return self._factory.confidential_allowed(ModelTier.FAST)

    async def extract(self, req: InsightRequest, ctx: CallContext) -> InsightOutcome:
        """Run the agent for one slice (``full``/``map``) or the merge (``reduce``)."""
        payload = _Input(
            mode=req.mode,
            filename=req.filename,
            kind_hint=req.kind_hint.value if req.kind_hint else None,
            chunks=[_Chunk(chunk_id=c.chunk_id, page=c.page, text=c.text) for c in req.chunks],
            partials=list(req.partials),
        )
        result = await self._factory.run(self._spec, payload, ctx)
        return InsightOutcome(
            insights=result.output,
            prompt_version=AgentFactory.version(self._spec),
            model=result.model,
            cost_usd=float(result.cost_usd),
        )
