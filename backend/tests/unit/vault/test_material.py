"""VaultService.material / passage: what analysis runs may read (verified content only, owner-scoped)."""

from __future__ import annotations

from typing import Any

from tests.unit.vault._env import OTHER, OWNER, VaultEnv, make_pages, pdf_bytes

from scout.vault.domain.enums import Retention

DATA = pdf_bytes(make_pages(3))


def _script(env: VaultEnv) -> None:
    def out(req: Any) -> dict[str, Any]:
        first = req.chunks[0]
        return {
            "kind": "declined_grant",
            "aims": [
                {"summary": "Verified aim", "chunk_ids": [first.chunk_id]},
                {"summary": "UNCITED", "chunk_ids": []},
            ],
            "findings": [{"summary": "Verified finding", "chunk_ids": [first.chunk_id]}],
            "keywords": ["lignin"],
        }

    env.llm.script("InsightExtractor", out)


async def test_material_has_verified_items_only_with_section_local_indices() -> None:
    env = VaultEnv()
    _script(env)
    doc = await env.upload(DATA, keep=True)
    await env.ingest(doc)
    m = await env.service().material(OWNER, doc.id)
    assert m.document is not None and m.keywords == ("lignin",)
    assert [(i.section, i.index, i.text) for i in m.insights] == [
        ("aims", 0, "Verified aim"),
        ("findings", 0, "Verified finding"),
    ]
    assert all("UNCITED" not in i.text for i in m.insights)
    assert len(m.passages) == 1 and m.passages[0].text and not m.passages[0].quotes  # keep: the passage text


async def test_discard_material_never_exposes_chunk_text_only_retained_quotes() -> None:
    env = VaultEnv()
    doc = await env.upload(DATA, keep=False)
    await env.ingest(doc)
    m = await env.service().material(OWNER, doc.id)
    assert m.document is not None and m.document.retention is Retention.DISCARD
    assert all(p.text is None for p in m.passages)


async def test_other_owners_and_deleting_documents_give_empty_material() -> None:
    env = VaultEnv()
    doc = await env.upload(DATA, keep=True)
    await env.ingest(doc)
    svc = env.service()
    assert (await svc.material(OTHER, doc.id)).document is None
    await svc.delete(OWNER, doc.id)
    empty = await svc.material(OWNER, doc.id)
    assert empty.document is None and empty.insights == () and empty.passages == ()


async def test_passage_lookup_is_owner_scoped() -> None:
    env = VaultEnv()
    doc = await env.upload(DATA, keep=True)
    await env.ingest(doc)
    svc = env.service()
    chunk_id = env.store.chunks[doc.id][0].chunk.id
    got = await svc.passage(OWNER, chunk_id)
    assert got is not None and got[0].id == doc.id
    assert await svc.passage(OTHER, chunk_id) is None
    await svc.delete(OWNER, doc.id)
    assert await svc.passage(OWNER, chunk_id) is None
