"""Erasure value objects: the pseudonymous subject hash and the ids a deletion must purge."""

from __future__ import annotations

import hashlib
import hmac
from uuid import UUID

from pydantic import BaseModel, ConfigDict


def subject_hash(key: str, account_id: UUID) -> str:
    """HMAC-SHA256 of the account id: the only trace an erased account leaves."""
    return hmac.new(key.encode(), str(account_id).encode(), hashlib.sha256).hexdigest()


class ErasureSubject(BaseModel):
    """Every id that must disappear with an account, collected while the account row still exists."""

    model_config = ConfigDict(frozen=True)

    account_id: UUID
    subject_hash: str
    researcher_id: UUID | None = None
    document_ids: frozenset[UUID] = frozenset()
    run_ids: frozenset[UUID] = frozenset()
    opportunity_ids: frozenset[UUID] = frozenset()
    session_ids: frozenset[UUID] = frozenset()
    mutual_match_ids: frozenset[UUID] = frozenset()

    def needles(self) -> list[str]:
        """String forms of all purge ids, without duplicates."""
        ids: list[UUID] = [self.account_id]
        if self.researcher_id is not None:
            ids.append(self.researcher_id)
        for group in (
            self.document_ids,
            self.run_ids,
            self.opportunity_ids,
            self.session_ids,
            self.mutual_match_ids,
        ):
            ids.extend(sorted(group))
        return list(dict.fromkeys(str(i) for i in ids))
