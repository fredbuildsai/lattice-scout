"""``DiscoveryService``: owner isolation, visibility, evidence opening, status changes and admin redaction."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from tests.unit.discovery._world import DOC_ID, OWNER, World

from scout.discovery.application.service import DiscoveryService
from scout.discovery.domain.enums import OppStatus, SourceType
from scout.shared import AccountId, InvalidStateTransition, NotFound, ValidationFailed

STRANGER = AccountId(UUID(int=999))


def service(w: World, *, quotes: bool = False) -> DiscoveryService:
    return DiscoveryService(w.uow, w.starter, w.vault, w.clock, w.ids, admin_show_vault_quotes=quotes)


async def finished() -> tuple[World, DiscoveryService, UUID]:
    w = World()
    run = await w.start()
    await w.run(run)
    svc = service(w)
    (card,) = (await svc.list_opportunities(OWNER, run_id=run.id)).items
    return w, svc, card.opportunity.id


async def test_a_stranger_sees_and_changes_nothing() -> None:
    w, svc, oid = await finished()
    run_id = next(iter(w.store.runs))
    assert (await svc.list_opportunities(STRANGER)).items == []
    for call in (
        svc.get_opportunity(STRANGER, oid),
        svc.set_status(STRANGER, oid, OppStatus.SAVED),
        svc.get_run(STRANGER, run_id),
        svc.cancel(STRANGER, run_id),
        svc.list_opportunities(STRANGER, run_id=run_id),
    ):
        with pytest.raises(NotFound):
            await call
    evidence_id = next(e.id for c in w.store.opportunities[oid].claims for e in c.evidence)
    with pytest.raises(NotFound):
        await svc.open_evidence(STRANGER, evidence_id)


async def test_status_changes_follow_the_user_statuses_only() -> None:
    _, svc, oid = await finished()
    assert (await svc.set_status(OWNER, oid, OppStatus.SAVED)).opportunity.status is OppStatus.SAVED
    assert (await svc.set_status(OWNER, oid, OppStatus.DISMISSED)).opportunity.status is OppStatus.DISMISSED
    assert (await svc.list_opportunities(OWNER)).items == []  # dismissed ones leave the default list
    assert len((await svc.list_opportunities(OWNER, status=OppStatus.DISMISSED)).items) == 1
    with pytest.raises(InvalidStateTransition):
        await svc.set_status(OWNER, oid, OppStatus.DUPLICATE)
    with pytest.raises(ValidationFailed):
        await svc.list_opportunities(OWNER, status=OppStatus.UNVERIFIED)
    with pytest.raises(ValidationFailed):
        await svc.list_opportunities(OWNER, cursor="not-a-cursor")


async def test_detail_carries_verified_claims_only() -> None:
    w, svc, oid = await finished()
    detail = await svc.get_opportunity(OWNER, oid)
    shown = {c.id for c in detail.rationale + detail.objection + detail.score}
    stored = w.store.opportunities[oid]
    assert shown == {c.id for c in stored.visible_claims()}
    assert detail.card.verified_sources >= 2


async def test_vault_evidence_opens_live_and_stops_when_the_document_is_deleted() -> None:
    w, svc, oid = await finished()
    vault_ev = next(
        e
        for c in w.store.opportunities[oid].visible_claims()
        for e in c.evidence
        if e.source_type is SourceType.VAULT
    )
    opened = await svc.open_evidence(OWNER, vault_ev.id)
    assert opened.kind == "vault" and opened.passage and opened.original_available  # keep: the passage text
    w.vault.delete(DOC_ID)
    with pytest.raises(NotFound):
        await svc.open_evidence(OWNER, vault_ev.id)


async def test_discard_documents_open_without_their_text() -> None:
    w = World()
    owner, mat, ready = w.vault.docs[DOC_ID]
    from dataclasses import replace

    p = replace(
        mat.passages[0], retention="discard", text=None, quotes=("develop lignin foams for insulation",)
    )
    w.vault.add(owner, replace(mat, retention="discard", passages=(p,)), ready)
    run = await w.start()
    await w.run(run)
    svc = service(w)
    (card,) = (await svc.list_opportunities(OWNER)).items
    ev = next(
        e for c in card.opportunity.visible_claims() for e in c.evidence if e.source_type is SourceType.VAULT
    )
    opened = await svc.open_evidence(OWNER, ev.id)
    assert opened.passage is None and not opened.original_available and opened.quote


async def test_cancel_a_finished_run_conflicts_and_a_queued_one_stops() -> None:
    w = World()
    run = await w.start()
    view = await service(w).cancel(OWNER, run.id)
    assert view.run.status.value == "cancelled"
    with pytest.raises(InvalidStateTransition):
        await service(w).cancel(OWNER, run.id)


async def test_admin_claims_hide_vault_quotes_unless_allowed_and_are_audited() -> None:
    w, svc, _ = await finished()
    admin = AccountId(uuid4())
    rows = (await svc.admin_claims(admin)).items
    assert rows and any(r.claim.status.value == "verified" for r in rows)
    assert not svc.admin_shows_vault_quotes and service(w, quotes=True).admin_shows_vault_quotes
    assert [e.action for e in w.store.audit.entries].count("admin.claims_viewed") == 1
    only = (await svc.admin_claims(admin, reason="no_such_reason")).items
    assert only == []


async def test_the_admin_row_redacts_vault_quotes_unless_the_flag_allows_them() -> None:
    from scout.api.admin import _row
    from scout.discovery.application.service import AdminClaimView

    w, _, oid = await finished()
    opp = w.store.opportunities[oid]
    claim = next(c for c in opp.claims if any(e.source_type is SourceType.VAULT for e in c.evidence))
    vault_quote = next(e.quote for e in claim.evidence if e.source_type is SourceType.VAULT)
    assert vault_quote
    hidden = _row(AdminClaimView(opp, claim), show_vault_quotes=False)
    shown = _row(AdminClaimView(opp, claim), show_vault_quotes=True)
    assert all(e.quote is None for e in hidden.evidence if e.source_type == "vault")
    assert any(e.quote == vault_quote for e in shown.evidence if e.source_type == "vault")
    assert any(e.quote for e in hidden.evidence if e.source_type == "literature")
