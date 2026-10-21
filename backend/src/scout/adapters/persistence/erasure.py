"""SQL adapters for account erasure: inventory, observability clean-up and the final transaction.

All text matching uses the purge ids of ``ErasureSubject`` (``LIKE ANY`` over ``%id%`` patterns), so rows in
tables without foreign keys (jobs, audit, analytics) are found by what they mention, not by what they claim.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from scout.adapters.persistence.db import Database
from scout.identity.domain.erasure import ErasureSubject, subject_hash
from scout.ports.audit import AuditEntry


def _n(res: Any) -> int:
    return int(res.rowcount or 0)


def _patterns(subject: ErasureSubject) -> list[str]:
    return [f"%{n}%" for n in subject.needles()]


async def _ids(s: AsyncSession, q: str, **p: Any) -> frozenset[UUID]:
    return frozenset(r[0] for r in await s.execute(text(q), p))


class SqlSubjectInventory:
    """Implements ``SubjectInventory``."""

    def __init__(self, db: Database, audit_key: str) -> None:
        self._db, self._key = db, audit_key

    async def collect(self, account_id: UUID) -> ErasureSubject:
        """Every id owned by the account that is still in the database."""
        async with self._db.sessionmaker() as s:
            researcher = (
                await s.execute(text("SELECT id FROM researcher WHERE account_id = :a"), {"a": account_id})
            ).scalar()
            return ErasureSubject(
                account_id=account_id,
                subject_hash=subject_hash(self._key, account_id),
                researcher_id=researcher,
                document_ids=await _ids(s, "SELECT id FROM document WHERE owner_id = :a", a=account_id),
                run_ids=await _ids(s, "SELECT id FROM analysis_run WHERE owner_id = :a", a=account_id),
                opportunity_ids=await _ids(s, "SELECT id FROM opportunity WHERE owner_id = :a", a=account_id),
                session_ids=await _ids(s, "SELECT id FROM app_session WHERE account_id = :a", a=account_id),
                mutual_match_ids=await _ids(
                    s,
                    "SELECT id FROM match_proposal WHERE state = 'mutual' AND (a_id = :a OR b_id = :a)",
                    a=account_id,
                ),
            )


class SqlObservabilityErasure:
    """Implements ``ObservabilityErasure``: delete analytics, anonymise usage rows (cost rows stay)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def erase(self, subject: ErasureSubject) -> dict[str, int]:
        """Delete events and sessions; null the user columns of ``llm_call`` and ``external_call``."""
        a, sessions = subject.account_id, list(subject.session_ids)
        out: dict[str, int] = {}
        async with self._db.sessionmaker() as s, s.begin():
            res = await s.execute(
                text("DELETE FROM analytics_event WHERE account_id = :a OR session_id = ANY(:s)"),
                {"a": a, "s": sessions},
            )
            out["events_deleted"] = _n(res)
            res = await s.execute(
                text("DELETE FROM app_session WHERE account_id = :a OR id = ANY(:s)"), {"a": a, "s": sessions}
            )
            out["sessions_deleted"] = _n(res)
            runs = list(subject.run_ids)
            anonymised = 0
            for table in ("llm_call", "external_call"):
                res = await s.execute(
                    text(
                        f"UPDATE {table} SET account_id = NULL, session_id = NULL, run_id = NULL, trace_id = NULL "  # noqa: S608
                        "WHERE account_id = :a OR session_id = ANY(:s) OR run_id = ANY(:r)"
                    ),
                    {"a": a, "s": sessions, "r": runs},
                )
                anonymised += _n(res)
            out["calls_anonymised"] = anonymised
        return out


class SqlAccountFinalizer:
    """Implements ``AccountFinalizer``: one transaction that leaves only the tombstone."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def finish(self, subject: ErasureSubject, own_job_id: UUID, entry: AuditEntry) -> dict[str, int]:
        """Delete jobs and audit rows that mention the user, write the tombstone, delete the account."""
        pat = _patterns(subject)
        async with self._db.sessionmaker() as s, s.begin():
            jobs = await s.execute(
                text(
                    "DELETE FROM job WHERE id <> :own AND kind <> 'account.delete' AND ("
                    "payload::text LIKE ANY(:p) OR coalesce(dedupe_key,'') LIKE ANY(:p) "
                    "OR coalesce(result::text,'') LIKE ANY(:p) OR coalesce(last_error,'') LIKE ANY(:p))"
                ),
                {"own": own_job_id, "p": pat},
            )
            audit = await s.execute(
                text(
                    "DELETE FROM audit_log WHERE action <> 'account.deleted' AND ("
                    "actor_id = :a OR coalesce(target,'') LIKE ANY(:p) OR meta::text LIKE ANY(:p))"
                ),
                {"a": subject.account_id, "p": pat},
            )
            await s.execute(
                text(
                    "INSERT INTO audit_log (id, actor_id, action, target, at, meta) "
                    "SELECT :id, NULL, 'account.deleted', NULL, :at, CAST(:meta AS jsonb) "
                    "WHERE NOT EXISTS (SELECT 1 FROM audit_log WHERE action = 'account.deleted' "
                    "AND meta->>'subject_hash' = :h)"
                ),
                {"id": entry.id, "at": entry.at, "meta": json.dumps(entry.meta), "h": subject.subject_hash},
            )
            await s.execute(text("DELETE FROM account WHERE id = :a"), {"a": subject.account_id})
            await s.execute(
                text(
                    "UPDATE job SET payload = jsonb_build_object('subject_hash', CAST(:h AS text), "
                    "'pass', coalesce(payload->'pass', '1'::jsonb)), last_error = NULL WHERE id = :own"
                ),
                {"h": subject.subject_hash, "own": own_job_id},
            )
        return {"jobs_deleted": _n(jobs), "audit_deleted": _n(audit)}


class SqlStuckErasures:
    """Implements ``StuckErasures``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def stuck(self, deleted_before: Any, limit: int) -> list[UUID]:
        """Soft-deleted accounts older than ``deleted_before``."""
        async with self._db.sessionmaker() as s:
            rows = await s.execute(
                text(
                    "SELECT id FROM account WHERE deleted_at IS NOT NULL AND deleted_at < :t ORDER BY deleted_at LIMIT :n"
                ),
                {"t": deleted_before, "n": limit},
            )
            return [r[0] for r in rows]
