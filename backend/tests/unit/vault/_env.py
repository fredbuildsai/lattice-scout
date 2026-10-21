"""Shared builders for the M2 vault use-case tests (plan M2 test-first; design docs/design/M2-vault-ingestion.md §5, §10).

Everything runs against in-memory fakes. The insight port is the real ``AgentsInsightExtractor`` over ``FakeLLM``
with a scripted output, wrapped by a recorder (calls, concurrency, fixed cost per call). Use-case classes are
imported lazily so one missing module only fails the tests that need it.
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import JsonValue
from tests.fakes import (
    FakeLLM,
    FakeTextExtractor,
    FixedClock,
    InMemoryFileStorage,
    InMemoryJobQueue,
    InMemoryUsageMeter,
    SequentialIdGenerator,
    VaultStore,
)
from tests.fakes.vault import InMemoryDocumentRepo, InMemoryVaultUoW

from scout.adapters.llm.factory import AgentFactory
from scout.ports.jobs import JobSpec, JobStatus, retry_delay
from scout.ports.llm import CallContext
from scout.ports.usage import ModelPrice, PriceTable
from scout.shared import AccountId, InfrastructureError, describe_exception
from scout.vault.adapters.insight_extractor import AgentsInsightExtractor
from scout.vault.domain.document import Document
from scout.vault.domain.enums import RetentionPolicy
from scout.vault.ports.insights import InsightOutcome, InsightRequest

if TYPE_CHECKING:
    from scout.vault.application.delete_job import DeleteDocumentJob
    from scout.vault.application.extract_insights import ExtractInsights
    from scout.vault.application.ingest import IngestDocument
    from scout.vault.application.service import VaultService
    from scout.vault.application.sweep import SweepVault
    from scout.vault.application.upload import UploadDocument

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "backend" / "tests" / "fixtures" / "llm"
OWNER = AccountId(UUID(int=0xA1))
OTHER = AccountId(UUID(int=0xB2))
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

QUOTE = "Reviewer 2 states that the sampling plan is insufficient for the claimed effect size"
FAKE_QUOTE = "This invented sentence never appears anywhere in the uploaded document text"
UNVERIFIED_MARK = "UNVERIFIED-ITEM-MARK"
CANARY = "CANARY-7f3a9c-ZETA"
COST_PER_CALL = 0.01


# ---------------------------------------------------------------------------- document bytes
def make_pages(n_pages: int = 3, *, canary: str | None = None, seed: int = 0) -> list[str]:
    """Pages of ~2.1k characters: three paragraphs each; page 1 holds ``QUOTE``; ``canary`` goes on page 2."""
    pages: list[str] = []
    for p in range(1, n_pages + 1):
        paras = []
        for k in range(1, 4):
            body = (
                f"Section {p}.{k} (seed {seed}) describes the lignin foam study design and its measurements. "
            )
            para = body * 7
            if p == 1 and k == 2:
                para = f"{QUOTE}. The panel agreed with this assessment. " + para
            if canary and p == min(2, n_pages) and k == 3:
                para = f"Internal note: {canary} must stay private. " + para
            paras.append(para.strip())
        pages.append("\n\n".join(paras))
    return pages


def pdf_bytes(pages: list[str]) -> bytes:
    """Bytes that sniff as PDF and that ``FakeTextExtractor`` reads back as the same pages."""
    return b"%PDF-1.7\n" + "\f".join(pages).encode("utf-8")


def pages_of(data: bytes) -> list[str]:
    """What ``FakeTextExtractor`` returns for ``data``."""
    return data.decode("utf-8").split("\f")


def docx_bytes(*, bomb: bool = False) -> bytes:
    """A minimal DOCX-shaped zip (``[Content_Types].xml`` + ``word/document.xml``); ``bomb`` adds a 5 MB zero entry."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
        if bomb:
            z.writestr("word/media/huge.bin", b"\0" * (5 * 1024 * 1024))
    return buf.getvalue()


# ---------------------------------------------------------------------------- scripted LLM output
def scripted_insights(req: Any) -> dict[str, Any]:
    """FakeLLM script (a function of the agent input). It includes unverified items on purpose.

    ``full``/``map``: one aim on the first chunk, one verified objection quoting ``QUOTE`` when a chunk holds it,
    one aim citing an unknown chunk (``UNVERIFIED_MARK``) and one objection with an invented quote.
    ``reduce``: merges what it was given (partials only), adds a cross-slice aim and an uncited finding.
    """
    if req.mode == "reduce":
        aims = [{"summary": a.summary, "chunk_ids": list(a.chunk_ids)} for p in req.partials for a in p.aims][
            :6
        ]
        objs = [
            {"quote": o.quote, "summary": o.summary, "chunk_ids": list(o.chunk_ids)}
            for p in req.partials
            for o in p.reviewer_objections
        ][:8]
        first = req.partials[0].aims[0].chunk_ids[0]
        last = req.partials[-1].aims[0].chunk_ids[0]
        aims.append({"summary": "Cross-slice aim", "chunk_ids": [first, last]})
        return {
            "kind": "declined_grant", "kind_reason": "Merged", "title": "Merged title", "aims": aims,
            "reviewer_objections": objs, "keywords": ["lignin"],
            "findings": [{"summary": "Reduce finding without citation", "chunk_ids": []}],
        }  # fmt: skip
    chunks = list(req.chunks)
    first = chunks[0].chunk_id
    objections = []
    for c in chunks:
        if QUOTE in c.text:
            objections.append(
                {"quote": QUOTE, "summary": "Sampling plan criticised", "chunk_ids": [c.chunk_id]}
            )
            break
    objections.append({"quote": FAKE_QUOTE, "summary": "Invented", "chunk_ids": [first]})
    return {
        "kind": "declined_grant", "kind_reason": "Reviewer comments present", "title": "Sample proposal",
        "aims": [
            {"summary": f"Aim from {first}", "chunk_ids": [first]},
            {"summary": UNVERIFIED_MARK, "chunk_ids": ["c999"]},
        ],
        "reviewer_objections": objections, "keywords": ["lignin", "foam"],
    }  # fmt: skip


def only_unverified(req: Any) -> dict[str, Any]:
    """FakeLLM script whose every item fails verification."""
    first = req.chunks[0].chunk_id
    return {
        "kind": "proposal", "kind_reason": "guess",
        "aims": [{"summary": "no citation", "chunk_ids": []}, {"summary": "bad ref", "chunk_ids": ["c999"]}],
        "reviewer_objections": [{"quote": FAKE_QUOTE, "summary": "x", "chunk_ids": [first]}],
    }  # fmt: skip


# ---------------------------------------------------------------------------- fault injection
@dataclass
class Faults:
    """Switches shared by every unit of work the use cases open."""

    commits: int = 0
    fail_commit_at: int | None = None  # the n-th commit since ``arm`` raises (nothing is published)
    fail_add: Exception | None = None  # ``documents.add`` raises this
    blind_once: bool = False  # the next ``find_active_by_sha`` returns None (simulates a lost race)

    def arm(self, n: int | None) -> None:
        """Count commits from now on; the ``n``-th one fails."""
        self.commits, self.fail_commit_at = 0, n


class _FaultyDocs(InMemoryDocumentRepo):
    def __init__(self, data: dict[UUID, Document], faults: Faults) -> None:
        super().__init__(data)
        self._faults = faults

    async def add(self, document: Document) -> None:
        if self._faults.fail_add is not None:
            raise self._faults.fail_add
        await super().add(document)

    async def find_active_by_sha(self, owner_id: AccountId, sha256: str) -> Document | None:
        if self._faults.blind_once:
            self._faults.blind_once = False
            return None
        return await super().find_active_by_sha(owner_id, sha256)


class FaultyVaultUoW(InMemoryVaultUoW):
    """The in-memory unit of work plus the injectable faults."""

    def __init__(self, store: VaultStore, faults: Faults) -> None:
        super().__init__(store)
        self._faults = faults

    async def __aenter__(self) -> FaultyVaultUoW:
        await super().__aenter__()
        self._documents = _FaultyDocs(self._d, self._faults)
        return self

    async def commit(self) -> None:
        self._faults.commits += 1
        if self._faults.fail_commit_at == self._faults.commits:
            raise RuntimeError("commit failed (injected)")
        await super().commit()


class FlakyJobQueue(InMemoryJobQueue):
    """Job queue whose ``enqueue`` can be switched off."""

    fail_enqueue = False

    async def enqueue(self, job: JobSpec) -> UUID:
        if self.fail_enqueue:
            raise InfrastructureError("queue unavailable")
        return await super().enqueue(job)


class HookedExtractor(FakeTextExtractor):
    """``FakeTextExtractor`` that runs a callback before extracting (to simulate concurrent changes)."""

    def __init__(self) -> None:
        super().__init__()
        self.hook: Callable[[], None] | None = None

    async def extract_text(self, data: bytes, mime: Any) -> Any:
        if self.hook is not None:
            self.hook()
        return await super().extract_text(data, mime)


class RecordingExtractor:
    """Wraps the real ``AgentsInsightExtractor``: records requests and concurrency, fixes the cost per call."""

    def __init__(self, inner: AgentsInsightExtractor, delay: float = 0.0) -> None:
        self.inner, self.delay = inner, delay
        self.requests: list[InsightRequest] = []
        self.in_flight = self.max_in_flight = 0

    async def extract(self, req: InsightRequest, ctx: CallContext) -> InsightOutcome:
        self.requests.append(req)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            out = await self.inner.extract(req, ctx)
        finally:
            self.in_flight -= 1
        return replace(out, cost_usd=COST_PER_CALL)

    def modes(self) -> list[str]:
        """Modes of the calls so far."""
        return [r.mode for r in self.requests]


# ---------------------------------------------------------------------------- the environment
@dataclass
class VaultEnv:
    """All fakes of one test; use-case builders share them."""

    clock: FixedClock = field(default_factory=lambda: FixedClock(T0))
    ids: SequentialIdGenerator = field(default_factory=lambda: SequentialIdGenerator(1000))
    store: VaultStore = field(default_factory=VaultStore)
    storage: InMemoryFileStorage = field(default_factory=InMemoryFileStorage)
    faults: Faults = field(default_factory=Faults)
    extractor: HookedExtractor = field(default_factory=HookedExtractor)
    full_max: int = 120_000
    slice_chars: int = 60_000
    concurrency: int = 3
    delay: float = 0.0

    def __post_init__(self) -> None:
        self.jobs = FlakyJobQueue(self.clock, SequentialIdGenerator(50_000))
        prices = PriceTable(version="t", models={"fake": ModelPrice(input=Decimal(1), output=Decimal(2))})
        self.llm = FakeLLM(FIXTURES, InMemoryUsageMeter(), prices, self.clock, SequentialIdGenerator(90_000))
        self.llm.script("InsightExtractor", scripted_insights)
        self.rec = RecordingExtractor(
            AgentsInsightExtractor(AgentFactory(self.llm, ROOT / "prompts")), self.delay
        )

    def uow(self) -> FaultyVaultUoW:
        """Unit-of-work factory."""
        return FaultyVaultUoW(self.store, self.faults)

    # builders -------------------------------------------------------------------------------
    def upload_uc(
        self,
        policy: RetentionPolicy = RetentionPolicy.USER_CHOICE,
        max_bytes: int = 10_000_000,
        max_documents: int = 50,
        max_per_day: int = 0,
    ) -> UploadDocument:
        """``UploadDocument`` over this environment."""
        from scout.vault.application.upload import UploadDocument

        return UploadDocument(
            uow=self.uow, storage=self.storage, jobs=self.jobs, clock=self.clock, ids=self.ids, policy=policy,
            max_bytes=max_bytes, max_documents=max_documents, max_per_day=max_per_day,
        )  # fmt: skip

    def insights_uc(self) -> ExtractInsights:
        """``ExtractInsights`` over the recording real extractor."""
        from scout.vault.application.extract_insights import ExtractInsights

        return ExtractInsights(
            self.rec, full_max=self.full_max, slice_chars=self.slice_chars, concurrency=self.concurrency
        )

    def ingest_uc(self) -> IngestDocument:
        """``IngestDocument`` over this environment."""
        from scout.vault.application.ingest import IngestDocument

        return IngestDocument(
            uow=self.uow, storage=self.storage, extractor=self.extractor, insights=self.insights_uc(),
            clock=self.clock,
        )  # fmt: skip

    def service(self) -> VaultService:
        """``VaultService``."""
        from scout.vault.application.service import VaultService

        return VaultService(uow=self.uow, jobs=self.jobs, clock=self.clock, ids=self.ids)

    def delete_job(self) -> DeleteDocumentJob:
        """``DeleteDocumentJob``."""
        from scout.vault.application.delete_job import DeleteDocumentJob

        return DeleteDocumentJob(uow=self.uow, storage=self.storage, clock=self.clock)

    def sweep(self, **kw: int) -> SweepVault:
        """``SweepVault``."""
        from scout.vault.application.sweep import SweepVault

        return SweepVault(uow=self.uow, jobs=self.jobs, clock=self.clock, **kw)

    # helpers --------------------------------------------------------------------------------
    async def upload(
        self,
        data: bytes,
        *,
        owner: AccountId = OWNER,
        keep: bool | None = True,
        policy: RetentionPolicy = RetentionPolicy.USER_CHOICE,
        filename: str = "proposal.pdf",
        **kw: Any,
    ) -> Document:
        """Upload through the real use case and return the document."""
        res = await self.upload_uc(policy, **kw).upload(owner, filename, data, keep, None)
        return res.document

    async def ingest(self, doc: Document, *, last: bool = False) -> dict[str, JsonValue]:
        """Run the ingest handler with the payload ``UploadDocument`` enqueues."""
        return await self.ingest_uc().handle(payload_for(doc), last_attempt=last)

    def doc(self, document_id: UUID) -> Document:
        """Committed document row."""
        return self.store.documents[document_id]

    def audit_actions(self) -> list[str]:
        """Committed audit actions, in order."""
        return [e.action for e in self.store.audit.entries]

    def audit_rows(self, action: str) -> list[Any]:
        """Committed audit entries of one action."""
        return [e for e in self.store.audit.entries if e.action == action]

    def snapshot(self, document_id: UUID) -> dict[str, Any]:
        """Comparable end state of one document (document fields, chunks, insights)."""
        d = self.doc(document_id)
        rows = self.store.chunks.get(document_id, [])
        return {
            "status": d.status, "failure_code": d.failure_code, "pages": d.pages, "chunk_count": d.chunk_count,
            "kind_suggested": d.kind_suggested, "original_deleted_at": d.original_deleted_at,
            "chunks": [(r.chunk, r.text, r.retained, r.redacted_at) for r in rows],
            "insights": self.store.insights.get(document_id),
            "object": d.storage_key in self.storage.objects,
        }  # fmt: skip


def payload_for(doc: Document) -> dict[str, JsonValue]:
    """The ``vault.ingest`` payload."""
    return {
        "account_id": str(doc.owner_id), "session_id": None, "feature": "vault.ingest",
        "document_id": str(doc.id),
    }  # fmt: skip


def dump(obj: Any) -> str:
    """Text of everything in ``obj`` (repr, plus JSON of pydantic-like values) for substring searches."""
    try:
        return json.dumps(obj, default=repr)
    except (TypeError, ValueError):
        return repr(obj)


Handler = Callable[..., Awaitable[dict[str, JsonValue]]]


async def drain(env: VaultEnv, handlers: dict[str, Handler], *, max_rounds: int = 30) -> int:
    """Run queued jobs of the given kinds like the worker does (retry back-off, ``last_attempt``); return runs.

    Moves the clock one minute forward whenever the only queued jobs are waiting for their retry time.
    """
    kinds, runs = list(handlers), 0
    for _ in range(max_rounds):
        job = await env.jobs.claim("test-worker", kinds)
        if job is None:
            waiting = [j for j in env.jobs.jobs.values() if j.kind in kinds and j.status is JobStatus.QUEUED]
            if not waiting:
                break
            env.clock.advance(timedelta(minutes=1))
            continue
        runs += 1
        try:
            result = await handlers[job.kind](job.payload, last_attempt=job.attempts >= job.max_attempts)
        except Exception as exc:
            await env.jobs.fail(job.id, describe_exception(exc), retry_delay(job.attempts))
        else:
            await env.jobs.complete(job.id, result)
    return runs
