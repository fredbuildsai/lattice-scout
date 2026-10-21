"""Row <-> domain mapping for discovery (JSON columns, scores, opportunities)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from scout.discovery.domain.budget import RunBudget, RunSpend
from scout.discovery.domain.enums import (
    CheckCode,
    ClaimSection,
    ClaimStatus,
    OppKind,
    OppStatus,
    RunStatus,
    RunStep,
    SetBy,
    SourceType,
    StepStatus,
)
from scout.discovery.domain.opportunity import Claim, Evidence, Opportunity, Scores
from scout.discovery.domain.run import AnalysisRun, Degradation, RunDocument, RunInputs, StepRecord, TopicRef
from scout.shared import AccountId


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def inputs_to_json(i: RunInputs) -> dict[str, Any]:
    """Serialise ``RunInputs`` (references only)."""
    return {
        "researcher_id": str(i.researcher_id) if i.researcher_id else None,
        "topics": [{"id": t.id, "name": t.name, "subfield": t.subfield, "weight": t.weight} for t in i.topics],
        "documents": [{"label": d.label, "document_id": str(d.document_id), "retention": d.retention} for d in i.documents],
        "insight_refs": list(i.insight_refs), "year_from": i.year_from, "novelty_from": i.novelty_from,
    }  # fmt: skip


def inputs_from_json(j: dict[str, Any]) -> RunInputs:
    """Inverse of ``inputs_to_json`` (an empty object gives empty inputs)."""
    return RunInputs(
        researcher_id=UUID(j["researcher_id"]) if j.get("researcher_id") else None,
        topics=tuple(TopicRef(t["id"], t.get("name"), t.get("subfield"), t["weight"]) for t in j.get("topics", [])),
        documents=tuple(RunDocument(d["label"], UUID(d["document_id"]), d["retention"]) for d in j.get("documents", [])),
        insight_refs=tuple(j.get("insight_refs", [])), year_from=j.get("year_from"), novelty_from=j.get("novelty_from"),
    )  # fmt: skip


def budget_to_json(b: RunBudget) -> dict[str, Any]:
    """Serialise the budget."""
    return {
        "usd": str(b.usd), "openalex_calls": b.openalex_calls, "max_corpus": b.max_corpus,
        "reserve_fast": str(b.reserve_fast), "reserve_reasoning": str(b.reserve_reasoning),
    }  # fmt: skip


def budget_from_json(j: dict[str, Any]) -> RunBudget:
    """Inverse of ``budget_to_json``."""
    return RunBudget(
        usd=Decimal(j["usd"]), openalex_calls=j["openalex_calls"], max_corpus=j["max_corpus"],
        reserve_fast=Decimal(j["reserve_fast"]), reserve_reasoning=Decimal(j["reserve_reasoning"]),
    )  # fmt: skip


def step_state_to_json(state: dict[RunStep, StepRecord]) -> dict[str, Any]:
    """Serialise the per-step records."""
    return {
        s.value: {
            "status": r.status.value, "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "done": r.done, "total": r.total, "summary": r.summary, "data": r.data,
        }
        for s, r in state.items()
    }  # fmt: skip


def step_state_from_json(j: dict[str, Any]) -> dict[RunStep, StepRecord]:
    """Inverse of ``step_state_to_json``."""
    return {
        RunStep(k): StepRecord(
            StepStatus(v["status"]), _dt(v["started_at"]), _dt(v["finished_at"]), v["done"], v["total"], v["summary"], v["data"]
        )
        for k, v in j.items()
    }  # fmt: skip


def run_values(r: AnalysisRun) -> dict[str, Any]:
    """Columns written by ``RunRepo.save``. Spend counters and ``heartbeat_at`` belong to the ledger and are never
    written here (a step's unit of work holds a stale copy)."""
    return {
        "owner_id": r.owner_id, "session_id": r.session_id, "status": r.status.value,
        "step": r.step.value if r.step else None, "step_state": step_state_to_json(r.step_state),
        "inputs": inputs_to_json(r.inputs), "budget": budget_to_json(r.budget), "generation": r.generation,
        "executions": r.executions, "partial": r.partial,
        "degraded": [{"step": d.step.value, "reason": d.reason} for d in r.degraded], "error_code": r.error_code,
        "error_step": r.error_step.value if r.error_step else None, "cancel_requested_at": r.cancel_requested_at,
        "prompt_versions": r.prompt_versions, "started_at": r.started_at, "finished_at": r.finished_at,
        "updated_at": r.updated_at or r.created_at,
    }  # fmt: skip


def run_from_row(row: Any) -> AnalysisRun:
    """Build the aggregate from an ``analysis_run`` row."""
    m = row._mapping
    return AnalysisRun(
        id=m["id"], owner_id=AccountId(m["owner_id"]), created_at=m["created_at"], budget=budget_from_json(m["budget"]),
        session_id=m["session_id"], status=RunStatus(m["status"]), step=RunStep(m["step"]) if m["step"] else None,
        step_state=step_state_from_json(m["step_state"]), inputs=inputs_from_json(m["inputs"]),
        spend=RunSpend(m["cost_usd"], m["llm_calls"], m["openalex_calls"], m["tokens_in"], m["tokens_out"]),
        generation=m["generation"], executions=m["executions"], partial=m["partial"],
        degraded=[Degradation(RunStep(d["step"]), d["reason"]) for d in m["degraded"]], error_code=m["error_code"],
        error_step=RunStep(m["error_step"]) if m["error_step"] else None,
        cancel_requested_at=m["cancel_requested_at"], prompt_versions=m["prompt_versions"],
        started_at=m["started_at"], finished_at=m["finished_at"], heartbeat_at=m["heartbeat_at"],
        updated_at=m["updated_at"],
    )  # fmt: skip


def _f(v: Any) -> float | None:
    return None if v is None else float(v)


def opportunity_values(o: Opportunity) -> dict[str, Any]:
    """Columns of the ``opportunity`` row."""
    s = o.scores
    return {
        "run_id": o.run_id, "owner_id": o.owner_id, "batch": o.batch, "ord": o.ord, "kind": o.kind.value,
        "title": o.title, "one_line": o.one_line, "status": o.status.value, "status_reason": o.status_reason,
        "status_step": o.status_step, "duplicate_of": o.duplicate_of, "cluster_ids": list(o.cluster_ids),
        "fit": round(s.fit, 3) if s else None, "novelty": round(s.novelty, 3) if s else None,
        "readiness": round(s.readiness, 3) if s else None, "effort": round(s.effort, 3) if s else None,
        "rank_score": round(s.rank, 3) if s else None, "score_basis": o.score_basis,
        "novelty_probe_id": o.novelty_probe_id, "readiness_checklist": o.readiness_checklist,
        "effort_estimate": o.effort_estimate, "next_steps": o.next_steps, "analyst_version": o.analyst_version,
        "critic_version": o.critic_version, "model": o.model, "verify_state": o.verify_state,
        "created_at": o.created_at, "updated_at": o.created_at,
    }  # fmt: skip


def claim_values(c: Claim, opportunity_id: UUID, now: datetime) -> dict[str, Any]:
    """Columns of the ``claim`` row."""
    return {
        "opportunity_id": opportunity_id, "ord": c.ord, "section": c.section.value, "text": c.text,
        "status": c.status.value, "status_reason": c.status_reason, "set_by": c.set_by.value, "agent": c.agent,
        "prompt_version": c.prompt_version, "model": c.model, "critic_verdict": c.critic_verdict,
        "critic_reason": c.critic_reason, "critic_fix": c.critic_fix, "created_at": now, "updated_at": now,
    }  # fmt: skip


def evidence_values(e: Evidence, claim_id: UUID) -> dict[str, Any]:
    """Columns of the ``evidence`` row."""
    return {
        "claim_id": claim_id, "ord": e.ord, "source_type": e.source_type.value, "label": e.label, "ref": e.ref,
        "quote": e.quote, "verified": e.verified, "check_code": e.check.value if e.check else None,
        "check_detail": e.check_detail, "corpus_work_id": e.corpus_work_id, "chunk_id": e.chunk_id,
        "document_id": e.document_id, "probe_id": e.probe_id, "insight_ref": e.insight_ref,
    }  # fmt: skip


def opportunity_from_rows(row: Any, claim_rows: list[Any], evidence_rows: list[Any]) -> Opportunity:
    """Build the aggregate from its rows."""
    m = row._mapping
    scores = (
        Scores(_f(m["fit"]) or 0.0, _f(m["novelty"]) or 0.0, _f(m["readiness"]) or 0.0, _f(m["effort"]) or 0.0, _f(m["rank_score"]) or 0.0)
        if m["rank_score"] is not None else None
    )  # fmt: skip
    by_claim: dict[UUID, list[Evidence]] = {}
    for e in sorted(evidence_rows, key=lambda r: r.ord):
        by_claim.setdefault(e.claim_id, []).append(
            Evidence(
                id=e.id,
                ord=e.ord,
                source_type=SourceType(e.source_type),
                label=e.label,
                ref=e.ref,
                quote=e.quote,
                verified=e.verified,
                check=CheckCode(e.check_code) if e.check_code else None,
                check_detail=e.check_detail,
                corpus_work_id=e.corpus_work_id,
                chunk_id=e.chunk_id,
                document_id=e.document_id,
                probe_id=e.probe_id,
                insight_ref=e.insight_ref,
            )  # fmt: skip
        )
    claims = [
        Claim(
            id=c.id,
            ord=c.ord,
            section=ClaimSection(c.section),
            text=c.text,
            status=ClaimStatus(c.status),
            status_reason=c.status_reason,
            set_by=SetBy(c.set_by),
            agent=c.agent,
            prompt_version=c.prompt_version,
            model=c.model,
            critic_verdict=c.critic_verdict,
            critic_reason=c.critic_reason,
            critic_fix=c.critic_fix,
            evidence=by_claim.get(c.id, []),
        )  # fmt: skip
        for c in sorted(claim_rows, key=lambda r: r.ord)
    ]
    return Opportunity(
        id=m["id"], run_id=m["run_id"], owner_id=AccountId(m["owner_id"]), batch=m["batch"], ord=m["ord"],
        kind=OppKind(m["kind"]), title=m["title"], one_line=m["one_line"], created_at=m["created_at"],
        status=OppStatus(m["status"]), status_reason=m["status_reason"], status_step=m["status_step"],
        duplicate_of=m["duplicate_of"], cluster_ids=tuple(m["cluster_ids"]), scores=scores, score_basis=m["score_basis"],
        novelty_probe_id=m["novelty_probe_id"], readiness_checklist=m["readiness_checklist"],
        effort_estimate=m["effort_estimate"], next_steps=m["next_steps"], analyst_version=m["analyst_version"],
        critic_version=m["critic_version"], model=m["model"], verify_state=m["verify_state"], claims=claims,
    )  # fmt: skip
