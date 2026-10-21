"""Anti-corruption layer: the only matching module that talks to the identity, profile and vault services."""

from __future__ import annotations

from scout.identity.application.service import IdentityService
from scout.matching.domain.agent_io import SignalItem
from scout.matching.domain.guards import clean_signal
from scout.matching.domain.profile import WorkIn
from scout.matching.ports.sources import Consent, ProfileInput
from scout.profile.application.service import ProfileNotFound, ProfileService
from scout.profile.domain.researcher import ImportStatus
from scout.shared import AccountId, NotFound
from scout.vault.application.service import VaultService
from scout.vault.domain.enums import DocStatus


class ProfileSourceAdapter:
    """``ProfileSource`` over ``ProfileService``."""

    def __init__(self, profile: ProfileService) -> None:
        self._profile = profile

    async def matching_input(self, account: AccountId) -> ProfileInput | None:
        """Included works (with their topics) and the researcher's country."""
        try:
            view = await self._profile.get_profile(account)
        except ProfileNotFound:
            return None
        r = view.researcher
        works = tuple(
            WorkIn(
                ref=f"work:{w.openalex_work_id.rsplit('/', 1)[-1]}", title=w.title, abstract=w.abstract,
                year=w.year, cited_by=w.cited_by, topics=tuple((t.id, t.name) for t in w.topics),
            )
            for w in view.works
            if w.included
        )  # fmt: skip
        return ProfileInput(r.id, r.country, r.import_status is ImportStatus.DONE, works)


class ConsentSourceAdapter:
    """``ConsentSource`` over ``IdentityService``."""

    def __init__(self, identity: IdentityService) -> None:
        self._identity = identity

    async def get(self, account: AccountId) -> Consent | None:
        """Consent of an active account."""
        try:
            a = await self._identity.get_account(account)
        except NotFound:
            return None
        if a.deleted_at is not None:
            return None
        return Consent(a.consent_matching, a.consent_vault_signals, a.launch_field)


class VaultSignalAdapter:
    """``VaultSignalSource`` over ``VaultService``: verified insight items only."""

    def __init__(self, vault: VaultService) -> None:
        self._vault = vault

    async def signals(self, account: AccountId, sections: tuple[str, ...], limit: int) -> list[SignalItem]:
        """Verified items of ``sections`` across ready documents, cleaned and bounded."""
        out: list[SignalItem] = []
        for doc in await self._vault.list(account):
            if doc.status is not DocStatus.READY:
                continue
            m = await self._vault.material(account, doc.id)
            for i in m.insights:
                if i.section in sections and len(out) < limit:
                    out.append(
                        SignalItem(ref=f"insight:{doc.id}.{i.section}[{i.index}]", text=clean_signal(i.text))
                    )
        return out
