"""Anti-corruption layer: the only discovery module that talks to the profile and vault services.

Maps their results to the discovery port DTOs, so the discovery domain and the other application modules never
import another context (enforced by import-linter).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from scout.discovery.ports.sources import (
    DocMaterial,
    DocumentCheck,
    InsightView,
    PassageView,
    ProfileWork,
    RunProfile,
)
from scout.profile.application.service import ProfileNotFound, ProfileService
from scout.profile.domain.researcher import ImportStatus
from scout.shared import AccountId, NotFound
from scout.vault.application.service import VaultService
from scout.vault.domain.enums import DocStatus, Retention
from scout.vault.domain.ids import ChunkId


def _ret(r: Retention) -> Literal["keep", "discard"]:
    return "keep" if r is Retention.KEEP else "discard"


class ProfileSourceAdapter:
    """``ProfileSource`` over ``ProfileService``."""

    def __init__(self, profile: ProfileService) -> None:
        self._profile = profile

    async def run_profile(self, owner_id: AccountId) -> RunProfile | None:
        """The claimed profile: included works (newest first) and topics."""
        try:
            view = await self._profile.get_profile(owner_id)
        except ProfileNotFound:
            return None
        r = view.researcher
        works = tuple(
            ProfileWork(
                openalex_id=w.openalex_work_id,
                title=w.title,
                abstract=w.abstract,
                year=w.year,
                cited_by=w.cited_by,
                type=w.type,
                doi=w.doi,
                topics=tuple((t.id, t.name, t.subfield) for t in w.topics),
            )  # fmt: skip
            for w in view.works
            if w.included
        )
        topics = tuple((t.topic_id, t.name, t.subfield, t.weight) for t in r.topics)
        return RunProfile(r.id, topics, works, import_done=r.import_status is ImportStatus.DONE)


class VaultSourceAdapter:
    """``VaultSource`` over ``VaultService`` (verified content only)."""

    def __init__(self, vault: VaultService) -> None:
        self._vault = vault

    async def check_documents(
        self, owner_id: AccountId, document_ids: tuple[UUID, ...]
    ) -> list[DocumentCheck]:
        """Owner-scoped existence and readiness."""
        out: list[DocumentCheck] = []
        for d in document_ids:
            try:
                doc, _ = await self._vault.get(owner_id, d)
            except NotFound:
                out.append(DocumentCheck(d, exists=False, ready=False))
                continue
            retention = _ret(doc.retention)
            out.append(
                DocumentCheck(d, exists=True, ready=doc.status is DocStatus.READY, retention=retention)
            )
        return out

    async def run_material(
        self, owner_id: AccountId, labelled: tuple[tuple[str, UUID], ...]
    ) -> list[DocMaterial]:
        """Verified insights and cited passages per document, labelled ``d1``, ``d2``..."""
        out: list[DocMaterial] = []
        for label, document_id in labelled:
            m = await self._vault.material(owner_id, document_id)
            if m.document is None:
                out.append(DocMaterial(document_id, label, "", "discard", active=False))
                continue
            retention = _ret(m.document.retention)
            insights = tuple(
                InsightView(
                    ref=f"{label}.{i.section}[{i.index}]",
                    section=i.section,
                    text=i.text,
                    chunk_labels=tuple(f"{label}.c{o}" for o in i.chunk_ords),
                    page_from=i.page_from,
                    page_to=i.page_to,
                )  # fmt: skip
                for i in m.insights
            )
            passages = tuple(
                PassageView(
                    label=f"{label}.c{p.ord}",
                    chunk_id=p.chunk_id,
                    document_id=document_id,
                    page_from=p.page_from,
                    page_to=p.page_to,
                    retention=retention,
                    text=p.text,
                    quotes=p.quotes,
                )  # fmt: skip
                for p in m.passages
            )
            out.append(
                DocMaterial(
                    document_id,
                    label,
                    m.document.filename,
                    retention,
                    active=True,
                    insights=insights,
                    passages=passages,
                    keywords=m.keywords,
                )  # fmt: skip
            )
        return out

    async def passage(self, owner_id: AccountId, chunk_id: UUID) -> PassageView | None:
        """One passage of an owned, active document."""
        found = await self._vault.passage(owner_id, ChunkId(chunk_id))
        if found is None:
            return None
        doc, p = found
        retention = _ret(doc.retention)
        return PassageView(
            label=f"c{p.ord}", chunk_id=p.chunk_id, document_id=doc.id, page_from=p.page_from, page_to=p.page_to,
            retention=retention, text=p.text, quotes=p.quotes,
        )  # fmt: skip
