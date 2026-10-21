"""Seed helper for the account-deletion scan (docs/design/M6-deletion.md section 4 "Seed").

``seed_world`` writes, with plain parametrised ``INSERT`` statements (one per table, so the schema stays the
specification), a needle row for user A in EVERY base table, plus the counterpart data that must survive:

* B: a pending proposal with A (A already accepted), B's own document, run, session, analytics, LLM calls, job
  and storage object;
* C: a MUTUAL proposal with A (two joint ideas) and C's star rating of it;
* D: a proposal DECLINED by D with a ``match_block`` (D blocks A).

Match ids are shared objects: they are not part of the scan needles (``Seeded.scan_needles`` leaves them out).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from scout.adapters.storage.local import LocalFileStorage
from scout.vault.domain.ids import storage_key

A_EMAIL = "alice-erase@example.edu"
A_NAME = "Alice Eraseme (sample)"
A_ORCID = "0000-0002-1825-0097"
B_EMAIL = "bob-keep@example.edu"
FIELD = "bio-based-materials"


class J:
    """Marks a value to be sent as ``jsonb``."""

    def __init__(self, value: Any) -> None:
        self.value = value


def ins(conn: Connection, table: str, **cols: Any) -> None:
    """INSERT one row; ``J`` values are cast to jsonb, everything else goes through the driver."""
    names = ", ".join(cols)
    holders = ", ".join(f"CAST(:{k} AS jsonb)" if isinstance(v, J) else f":{k}" for k, v in cols.items())
    params = {k: json.dumps(v.value) if isinstance(v, J) else v for k, v in cols.items()}
    conn.execute(text(f"INSERT INTO {table} ({names}) VALUES ({holders})"), params)  # noqa: S608


def pair(x: UUID, y: UUID) -> tuple[UUID, UUID]:
    """``(a_id, b_id)`` in the order the schema requires (``a_id < b_id``)."""
    return (x, y) if x.int < y.int else (y, x)


def side_of(x: UUID, a_id: UUID) -> str:
    """``'a'`` or ``'b'``: the side ``x`` plays in a proposal whose ``a_id`` is ``a_id``."""
    return "a" if x == a_id else "b"


@dataclass
class Seeded:
    """The ids written by ``seed_world``."""

    a: UUID
    b: UUID
    c: UUID
    d: UUID
    start: datetime
    researcher: UUID = field(default_factory=uuid4)
    docs: list[UUID] = field(default_factory=lambda: [uuid4(), uuid4()])
    run: UUID = field(default_factory=uuid4)
    opportunities: list[UUID] = field(default_factory=lambda: [uuid4(), uuid4()])
    session: UUID = field(default_factory=uuid4)
    pending: UUID = field(default_factory=uuid4)
    mutual: UUID = field(default_factory=uuid4)
    declined: UUID = field(default_factory=uuid4)
    llm_calls: list[UUID] = field(default_factory=lambda: [uuid4(), uuid4(), uuid4()])
    external_calls: list[UUID] = field(default_factory=lambda: [uuid4(), uuid4()])
    b_doc: UUID = field(default_factory=uuid4)
    b_run: UUID = field(default_factory=uuid4)
    b_session: UUID = field(default_factory=uuid4)
    b_researcher: UUID = field(default_factory=uuid4)
    orphan_key: str = ""

    @property
    def scan_needles(self) -> list[str]:
        """Strings that must not occur anywhere after deletion (purge ids except match ids, plus personal data)."""
        ids = [self.a, self.researcher, *self.docs, self.run, *self.opportunities, self.session]
        return [str(i) for i in ids] + [A_EMAIL, A_NAME, A_ORCID]

    @property
    def a_storage_keys(self) -> list[str]:
        return [storage_key(self.a, d) for d in self.docs] + [self.orphan_key]


def _rationale(doc: UUID) -> J:
    """Stored rationale whose source references point at A's document (must be cleared on detach)."""
    item = lambda t: {"text": t, "source_refs": [f"doc:{doc}", "W-ref-1"]}  # noqa: E731
    return J(
        {
            "source": "template",
            "you_bring": [item("lignin chemistry")],
            "they_bring": [item("foam rheology")],
            "why": "Complementary methods.",
            "prompt_version": None,
        }
    )


def _proposal(conn: Connection, s: Seeded, pid: UUID, other: UUID, state: str, **extra: Any) -> None:
    a_id, b_id = pair(s.a, other)
    ins(
        conn, "match_proposal", id=pid, a_id=a_id, b_id=b_id, field=FIELD, score=0.5,
        score_basis=J({"topic_overlap": 0.4, "method_overlap": 0.2, "activity": 0.75, "alpha": 1.0, "beta": 1.5,
                       "shared_topics": ["lignin"], "a_only": ["m1"], "b_only": ["m2"], "formula": "c1"}),
        rationale=_rationale(s.docs[0]),
        a_public=J({"region": "europe", "career_band": "mid"}), b_public=J({"region": "asia", "career_band": "early"}),
        state=state, expires_at=s.start + timedelta(days=14), **extra,
    )  # fmt: skip


def seed_world(conn: Connection, s: Seeded, storage_dir: Path) -> None:
    """Write user A's needle rows in every table, the counterparts' data, and the storage objects.

    Accounts A and B must already exist (created by the API on first call); C and D are inserted here.
    """
    a, b, now = s.a, s.b, s.start
    for acc, email in ((s.c, "carol-mutual@example.edu"), (s.d, "dan-decliner@example.edu")):
        ins(conn, "account", id=acc, email=email)
    conn.execute(
        text("UPDATE account SET consent_matching = true WHERE id = ANY(:ids)"), {"ids": [a, b, s.c, s.d]}
    )

    # ---- profile
    ins(conn, "researcher", id=s.researcher, account_id=a, display_name=A_NAME, orcid=A_ORCID,
        institution="Example University", import_status="done", import_generation=1)  # fmt: skip
    ins(conn, "researcher_openalex_id", id=uuid4(), researcher_id=s.researcher, openalex_author_id="A5000000001",
        basis="primary", display_name=A_NAME, orcid=A_ORCID)  # fmt: skip
    ins(conn, "claimed_work", id=uuid4(), researcher_id=s.researcher, openalex_work_id="W5000000001",
        title="Lignin foams", abstract="We study lignin foams.")  # fmt: skip
    ins(conn, "researcher_topic", id=uuid4(), researcher_id=s.researcher, openalex_topic_id="T1", weight=0.5)
    ins(conn, "researcher_method", id=uuid4(), researcher_id=s.researcher, method_term="rheometry", weight=0.4,
        source="vault")  # fmt: skip
    ins(conn, "researcher", id=s.b_researcher, account_id=b, display_name="Bob Keeper (sample)")

    # ---- observability (A, pre-login rows linked by session, and B)
    ins(conn, "app_session", id=s.session, account_id=a, started_at=now, last_seen_at=now)
    ins(conn, "app_session", id=s.b_session, account_id=b, started_at=now, last_seen_at=now)
    events: tuple[tuple[str, UUID | None, UUID, dict[str, str]], ...] = (
        ("page_view", a, s.session, {"page": "/profile", "email": A_EMAIL}),
        ("page_view", None, s.session, {"page": "/login"}),  # pre-login event of the same session
        ("page_view", b, s.b_session, {"page": "/matches"}),
    )
    for name, ev_acc, ev_sess, props in events:
        conn.execute(
            text("INSERT INTO analytics_event (id, ts, account_id, session_id, name, properties)"
                 " VALUES (:i, now(), :a, :s, :n, CAST(:p AS jsonb))"),
            {"i": uuid4(), "a": ev_acc, "s": ev_sess, "n": name, "p": json.dumps(props)},
        )  # fmt: skip
    llm_rows: tuple[tuple[UUID, UUID | None, UUID, UUID | None], ...] = (
        (s.llm_calls[0], a, s.session, s.run),
        (s.llm_calls[1], None, s.session, None),
        (s.llm_calls[2], b, s.b_session, s.b_run),
    )
    for call_id, call_acc, call_sess, call_run in llm_rows:
        conn.execute(
            text("INSERT INTO llm_call (id, ts, account_id, session_id, run_id, feature, agent, route, provider,"
                 " model, input_tokens, output_tokens, latency_ms, cost_usd, status, prompt_version, trace_id)"
                 " VALUES (:i, now(), :a, :s, :r, 'discovery', 'GapAnalyst', 'smart', 'fake', 'fake-1', 10, 5, 20,"
                 " 0.01, 'ok', 'v1', 'trace-1')"),
            {"i": call_id, "a": call_acc, "s": call_sess, "r": call_run},
        )  # fmt: skip
    ext_rows = ((s.external_calls[0], a, s.session, s.run), (s.external_calls[1], b, s.b_session, s.b_run))
    for call_id, acc, sess, run in ext_rows:
        conn.execute(
            text("INSERT INTO external_call (id, ts, account_id, session_id, run_id, service, endpoint, status,"
                 " latency_ms, cost_usd, trace_id)"
                 " VALUES (:i, now(), :a, :s, :r, 'openalex', '/works', 'ok', 30, 0.002, 'trace-2')"),
            {"i": call_id, "a": acc, "s": sess, "r": run},
        )  # fmt: skip

    # ---- vault
    keep, discard = s.docs
    for doc, retention, sha in ((keep, "keep", "1"), (discard, "discard", "2")):
        ins(conn, "document", id=doc, owner_id=a, filename="note.pdf", mime="application/pdf", size_bytes=100,
            sha256=sha * 64, storage_key=storage_key(a, doc), retention=retention, status="ready", pages=1,
            page_basis="physical", chunk_count=1)  # fmt: skip
    chunk = uuid4()
    ins(conn, "document_chunk", id=chunk, document_id=keep, ord=0, label="C1", page_from=1, page_to=1,
        char_start=0, char_end=40, text="Private lignin foam aim.", summary="aim")  # fmt: skip
    ins(conn, "document_chunk", id=uuid4(), document_id=discard, ord=0, label="C1", page_from=1, page_to=1,
        char_start=0, char_end=40, redacted_at=now)  # fmt: skip
    for doc in (keep, discard):
        ins(conn, "document_insights", document_id=doc, payload=J({"aims": [{"summary": "UNCITED-CLAIM"}]}),
            verified_count=0, unverified_count=1, prompt_version="v1", model="fake-1", mode="full", slices=1,
            cost_usd=0)  # fmt: skip
    b_doc_row = dict(id=s.b_doc, owner_id=b, filename="b.pdf", mime="application/pdf", size_bytes=10,
                     sha256="3" * 64, storage_key=storage_key(b, s.b_doc), retention="keep", status="ready")  # fmt: skip
    ins(conn, "document", **b_doc_row)

    # ---- discovery
    for run, owner, sess in ((s.run, a, s.session), (s.b_run, b, s.b_session)):
        ins(conn, "analysis_run", id=run, owner_id=owner, session_id=sess, status="done", budget=J({}))
    ins(conn, "run_document", run_id=s.run, document_id=keep, label="D1")
    cluster, work, probe = uuid4(), uuid4(), uuid4()
    ins(conn, "cluster", id=cluster, run_id=s.run, key="k1", label_key="k1", label="Lignin", label_source="topic",
        size=3)  # fmt: skip
    ins(conn, "corpus_work", id=work, run_id=s.run, openalex_work_id="W7", title="Lignin review", origin="search",
        cluster_id=cluster)  # fmt: skip
    ins(conn, "novelty_probe", id=probe, run_id=s.run, label="p1", cluster_id=cluster, query="lignin foam",
        from_date=now.date(), count=4, status="measured")  # fmt: skip
    for i, opp in enumerate(s.opportunities):
        ins(conn, "opportunity", id=opp, run_id=s.run, owner_id=a, batch=0, ord=i, kind="white_spot",
            title=f"Gap {i}", status="shown", cluster_ids=[cluster], novelty_probe_id=probe)  # fmt: skip
    claim_ok, claim_bad = uuid4(), uuid4()
    ins(conn, "claim", id=claim_ok, opportunity_id=s.opportunities[0], ord=0, section="rationale",
        text="Verified claim", status="verified", set_by="verify_code")  # fmt: skip
    ins(conn, "claim", id=claim_bad, opportunity_id=s.opportunities[0], ord=1, section="rationale",
        text="UNCITED-CLAIM unverified", status="unverified", set_by="verify_code")  # fmt: skip
    ins(conn, "evidence", id=uuid4(), claim_id=claim_ok, ord=0, source_type="literature", label="Review",
        ref="W7", verified=True, corpus_work_id=work)  # fmt: skip
    ins(conn, "evidence", id=uuid4(), claim_id=claim_ok, ord=1, source_type="vault", label="D1", ref="C1",
        quote="Private lignin foam aim.", verified=True, chunk_id=chunk, document_id=keep)  # fmt: skip
    ins(conn, "evidence", id=uuid4(), claim_id=claim_bad, ord=0, source_type="vault", label="D1", ref="C9",
        verified=False, document_id=keep)  # fmt: skip

    # ---- matching profile
    ins(conn, "match_profile", account_id=a, researcher_id=s.researcher, field=FIELD, region="europe",
        career_band="mid", activity=0.5, eligible=True, topics_hash="h", methods_hash="h",
        vocabulary_version="v1", methods_prompt_version="v1", computed_at=now)  # fmt: skip
    ins(conn, "match_profile_topic", account_id=a, topic_id="T1", name="Lignin", weight=0.5)
    ins(conn, "match_profile_method", account_id=a, method_id="rheometry", weight=0.4, source="vault")
    ins(
        conn,
        "match_method_proposal",
        id=uuid4(),
        account_id=a,
        label="new method",
        source_refs=[f"doc:{keep}"],
    )

    # ---- proposals: B pending (A accepted), C mutual, D declined by D
    a_is_lo = pair(a, b)[0] == a
    _proposal(conn, s, s.pending, b, "a_accepted" if a_is_lo else "b_accepted",
              **{"a_decision" if a_is_lo else "b_decision": "accept",
                 "a_decided_at" if a_is_lo else "b_decided_at": now})  # fmt: skip
    _proposal(conn, s, s.mutual, s.c, "mutual", a_decision="accept", b_decision="accept", revealed_at=now,
              ideas_status="ready")  # fmt: skip
    for i in range(2):
        ins(conn, "joint_idea", id=uuid4(), match_id=s.mutual, ord=i, title=f"Idea {i}", question="?",
            uses_from_a=J(["x"]), uses_from_b=J(["y"]), first_step="call", possible_output="paper")  # fmt: skip
    d_side = side_of(s.d, pair(a, s.d)[0])
    _proposal(conn, s, s.declined, s.d, "declined", **{f"{d_side}_decision": "decline"}, declined_by=d_side,
              close_reason="declined", never_similar_by=d_side, closed_at=now)  # fmt: skip
    ins(conn, "match_block", account_id=s.d, blocked_account_id=a, match_id=s.declined, created_at=now)

    # ---- notifications: preference row and a delivery-log row whose ref is A's run id
    ins(conn, "notification_pref", account_id=a, email_runs=True, email_matches=True, email_digest=True)
    ins(conn, "notification", id=uuid4(), account_id=a, template="run_finished", ref=str(s.run), sent_at=now)

    # ---- feedback: A on an opportunity and on the mutual match; C (counterpart) rates the mutual match
    ins(conn, "feedback", id=uuid4(), author_id=a, target_type="opportunity", target_id=s.opportunities[0],
        rating="up", reasons=[], created_at=now, updated_at=now)  # fmt: skip
    ins(conn, "feedback", id=uuid4(), author_id=a, target_type="match", target_id=s.mutual, stars=4,
        reasons=[], created_at=now, updated_at=now)  # fmt: skip
    ins(conn, "feedback", id=uuid4(), author_id=s.c, target_type="match", target_id=s.mutual, stars=5,
        reasons=[], comment="Great match", created_at=now, updated_at=now)  # fmt: skip

    # ---- jobs (finished ones and a queued one far in the future) and audit rows
    ins(conn, "job", id=uuid4(), kind="vault.delete", payload=J({"document_id": str(keep)}), status="succeeded",
        result=J({"deleted": True}), dedupe_key=str(keep))  # fmt: skip
    ins(conn, "job", id=uuid4(), kind="matching.reveal", payload=J({"match_id": str(s.mutual), "account_id": str(a)}),
        status="succeeded", result=J({"ideas": 2}))  # fmt: skip
    ins(conn, "job", id=uuid4(), kind="profile.import", payload=J({"researcher_id": str(s.researcher)}),
        status="succeeded", result=J({"works": 1}), last_error=f"earlier failure for {A_EMAIL}")  # fmt: skip
    ins(conn, "job", id=uuid4(), kind="matching.profile", payload=J({"account_id": str(a)}), status="queued",
        run_at=now + timedelta(days=30), dedupe_key=f"profile:{a}")  # fmt: skip
    ins(
        conn,
        "job",
        id=uuid4(),
        kind="profile.import",
        payload=J({"researcher_id": str(s.b_researcher)}),
        status="succeeded",
        result=J({"works": 2}),
    )  # B's job must survive
    audit = (
        (a, "consent.changed", str(a), {"consent": "matching"}),
        (a, "account.created", str(a), {"email": A_EMAIL}),
        (b, "profile.claim_conflict", None, {"other_account": str(a)}),  # B is the actor, A is mentioned
        (None, "match.mutual", str(s.mutual), {"a_id": str(a), "b_id": str(s.c)}),
        (b, "consent.changed", str(b), {"consent": "matching"}),  # B's own: stays
    )
    for actor, action, target, meta in audit:
        ins(conn, "audit_log", id=uuid4(), actor_id=actor, action=action, target=target, at=now, meta=J(meta))

    # ---- storage: 2 documents and 1 orphan for A, 1 object for B
    s.orphan_key = f"vault/{a}/{uuid4()}"
    store = LocalFileStorage(storage_dir)

    async def put_all() -> None:
        for key in (storage_key(a, keep), storage_key(a, discard), s.orphan_key, storage_key(b, s.b_doc)):
            await store.put(key, b"%PDF-sample", "application/pdf")

    asyncio.run(put_all())


def utcnow() -> datetime:
    """Real current time (partitioned tables only accept rows for existing partitions)."""
    return datetime.now(UTC)
