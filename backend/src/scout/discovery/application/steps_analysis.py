"""Pipeline steps 5-8: measure_novelty, analyse, verify, score_persist (DECISIONS 58-62, 65)."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date, datetime
from functools import partial
from typing import cast
from uuid import UUID

import structlog

from scout.discovery.application.pipeline import StepContext, to_corpus
from scout.discovery.application.steps_retrieval import Summary, _degrade
from scout.discovery.domain.agent_io import (
    ChunkForAnalyst,
    ClaimVerdict,
    CoverageRow,
    CriticClaim,
    CriticDraft,
    CriticInput,
    CriticVerdict,
    EvidenceText,
    GapAnalysis,
    GapContext,
    InsightForAnalyst,
    NoveltyRow,
    OpportunityDraft,
    PaperForAnalyst,
    ProfileSummary,
    RecentWork,
    SimilarPaper,
)
from scout.discovery.domain.budget import BudgetExhausted, OpenAlexCapReached
from scout.discovery.domain.corpus import Cluster, CorpusWork, NoveltyProbe
from scout.discovery.domain.enums import (
    CheckCode,
    ClaimSection,
    ClaimStatus,
    CorpusOrigin,
    OppKind,
    OppStatus,
    RunStep,
    SetBy,
    SourceType,
)
from scout.discovery.domain.ids import (
    claim_id,
    corpus_work_id,
    evidence_id,
    opportunity_id,
    probe_id,
)
from scout.discovery.domain.novelty import novelty_score, probe_queries
from scout.discovery.domain.opportunity import Claim, Evidence, Opportunity, Scores
from scout.discovery.domain.run import AnalysisRun
from scout.discovery.domain.scoring import (
    effort_value,
    fit_score,
    is_duplicate,
    jaccard,
    rank_score,
    readiness_score,
    title_tokens,
)
from scout.discovery.domain.verification import (
    EMAIL,
    CheckResult,
    ChunkMaterial,
    EvidenceRef,
    InsightMaterial,
    ProbeMaterial,
    RunMaterialIndex,
    WorkMaterial,
    check_claim,
    check_ref,
    claim_passes,
)
from scout.discovery.ports.agents import AgentOutcome
from scout.discovery.ports.repos import DiscoveryUnitOfWork
from scout.discovery.ports.sources import DocMaterial
from scout.ports.literature import WorkQuery
from scout.ports.llm import LLMOutputInvalid, LLMUnavailable
from scout.shared import InvariantViolation
from scout.shared.text import clean_text

log = structlog.get_logger()
_LLM_ERRORS = (LLMUnavailable, LLMOutputInvalid)


def _doc_label(ref: str) -> str:
    return ref.split(".", 1)[0]


# ---- 5. measure_novelty --------------------------------------------------------------------------------------------
async def measure_novelty(c: StepContext) -> Summary:
    """Count matching works for each candidate cluster (at most two probes each); the model never guesses a count."""
    run = await c.read()
    step = RunStep.MEASURE_NOVELTY
    async with c.uow() as uow:
        clusters = [cl for cl in await uow.clusters.list(c.run_id) if cl.candidate_rank is not None]
        existing = {p.label: p for p in await uow.probes.list(c.run_id)}
    clusters.sort(key=lambda cl: (cl.candidate_rank or 0, cl.key))
    material = [m for m in await c.material(run) if m.active]
    kw = Counter(k.lower() for m in material for k in m.keywords)
    top_keyword = sorted(kw.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if kw else None
    since = run.inputs.novelty_from or (c.clock.now().year - c.field.novelty.years_back)

    plan: list[NoveltyProbe] = []
    for cl in clusters:
        for q in probe_queries(cl.search_terms, top_keyword):
            label = f"n{len(plan) + 1}"
            old = existing.get(label)
            plan.append(
                old or NoveltyProbe(probe_id(c.run_id, label), c.run_id, label, cl.id, q, date(since, 1, 1))
            )
    todo = [p for p in plan if p.status == "pending"]
    cap_hit = False
    wave = c.cfg.openalex_concurrency

    async def one(p: NoveltyProbe) -> int:
        async with c._oa_sem:
            return await c.literature.count_works(
                WorkQuery(
                    search=p.query,
                    from_publication_date=p.from_date.isoformat(),
                    type=c.field.query.type,
                    per_page=1,
                )
            )

    for i in range(0, len(todo), wave):
        batch = todo[i : i + wave]
        results = await asyncio.gather(*(one(p) for p in batch), return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException) and not isinstance(r, Exception):
                raise r
        now = c.clock.now()
        for p, r in zip(batch, results, strict=True):
            if isinstance(r, int):
                p.count, p.status, p.measured_at = r, "measured", now
            elif isinstance(r, OpenAlexCapReached):
                cap_hit = True
        hard = [r for r in results if isinstance(r, Exception) and not isinstance(r, OpenAlexCapReached)]

        async def write(
            uow: DiscoveryUnitOfWork,
            r: AnalysisRun,
            batch: list[NoveltyProbe] = batch,
            now: datetime = now,
            i: int = i,
        ) -> None:
            await uow.probes.upsert_many(batch)
            r.record_progress(step, c.generation, min(i + wave, len(todo)), len(todo), now)

        await c.tx(write)
        if hard:
            raise hard[0]
        if cap_hit:
            break
    for p in todo:
        if p.status == "pending":
            p.status = "skipped"
    measured_clusters = {p.cluster_id for p in plan if p.status == "measured"}
    kept = [cl.key for cl in clusters if cl.id in measured_clusters]

    async def finish(uow: DiscoveryUnitOfWork, r: AnalysisRun) -> None:
        await uow.probes.upsert_many(plan)
        r.step_state[step].data["candidates"] = kept
        if cap_hit or len(kept) < len(clusters):
            await _degrade(r, c, step, "openalex_cap")

    await c.tx(finish)
    return {"probes": sum(1 for p in plan if p.status == "measured"), "candidates": len(kept)}


# ---- 6. analyse ----------------------------------------------------------------------------------------------------
def _analyst_context(
    c: StepContext, run: AnalysisRun, batch: list[Cluster], probes: list[NoveltyProbe], corpus: list[CorpusWork],
    material: list[DocMaterial], exclude: list[str],
) -> GapContext:  # fmt: skip
    queries = run.step_state[RunStep.PLAN_QUERIES].data["queries"]
    objection = {q["label"] for q in queries if q["purpose"] == "objection"}
    papers: dict[str, CorpusWork] = {}
    for cl in batch:
        members = sorted(
            (w for w in corpus if w.cluster_id == cl.id), key=lambda w: (-w.cited_by, w.openalex_id)
        )
        papers.update({w.openalex_id: w for w in members[:5]})
    for label in sorted(objection):
        hits = sorted(
            (w for w in corpus if label in w.query_labels), key=lambda w: (-w.cited_by, w.openalex_id)
        )
        papers.update({w.openalex_id: w for w in hits[:3]})
    chunks: list[ChunkForAnalyst] = []
    total = 0
    for m in material:
        for p in m.passages:
            text = p.text or "\n".join(p.quotes)
            if not text or len(chunks) >= 40 or total + len(text) > 30_000:
                continue
            total += len(text)
            chunks.append(ChunkForAnalyst(chunk_id=p.label, document=m.label, page=p.page_from, text=text))
    profile_works = [w for w in corpus if w.origin is CorpusOrigin.PROFILE]
    profile_works.sort(key=lambda w: (-(w.year or 0), w.openalex_id))
    batch_ids = {cl.id for cl in batch}
    return GapContext(
        profile_summary=ProfileSummary(
            topics=[clean_text(t.name or t.id, 80) for t in run.inputs.topics[:25]],
            recent_works=[RecentWork(work_id=w.openalex_id, title=w.title[:200], year=w.year) for w in profile_works[:15]],
        ),
        insights=[
            InsightForAnalyst(insight_ref=i.ref, document=m.label, section=i.section, text=i.text)
            for m in material
            for i in m.insights
        ][:60],
        coverage=[
            CoverageRow(
                cluster_id=cl.label_key, label=cl.label, corpus_count=cl.size, user_works=cl.user_works,
                user_insights=cl.insight_weight, density=cl.density,
            )
            for cl in batch
        ],
        novelty=[
            NoveltyRow(
                candidate_id=p.label, cluster_id=next(cl.label_key for cl in batch if cl.id == p.cluster_id),
                query=p.query[:200], since_year=p.from_date.year, count=cast(int, p.count),
            )
            for p in probes
            if p.status == "measured" and p.cluster_id in batch_ids
        ],
        papers=[
            PaperForAnalyst(work_id=w.openalex_id, title=w.title[:200], year=w.year, abstract=(w.abstract or "")[:1200])
            for w in list(papers.values())[:40]
        ],
        chunks=chunks,
        max_opportunities=5,
        exclude_titles=[t[:120] for t in exclude][:40],
    )  # fmt: skip


def _to_opportunity(
    c: StepContext, d: OpportunityDraft, batch_no: int, ord_: int, clusters: dict[str, Cluster],
    origins: dict[str, CorpusOrigin], version: str, model: str, now: datetime,
) -> Opportunity:  # fmt: skip
    oid = opportunity_id(c.run_id, batch_no, ord_)
    claims: list[Claim] = []
    for i, cd in enumerate(d.claims):
        cid = claim_id(oid, i)
        evidence: list[Evidence] = []
        for j, ref in enumerate(cd.evidence_refs):
            if ref.type == "work":
                st = (
                    SourceType.PROFILE
                    if origins.get(ref.id) is CorpusOrigin.PROFILE
                    else SourceType.LITERATURE
                )
            elif ref.type == "count":
                st = SourceType.COUNT
            else:
                st = SourceType.VAULT
            evidence.append(
                Evidence(
                    id=evidence_id(cid, j),
                    ord=j,
                    source_type=st,
                    label=ref.id,
                    ref=ref.id,
                    quote=ref.quote,
                    insight_ref=ref.id if ref.type == "insight" else None,
                )  # fmt: skip
            )
        claim = Claim(
            id=cid, ord=i, section=ClaimSection(cd.section), text=cd.text, agent="GapAnalyst",
            prompt_version=version, model=model, evidence=evidence,
        )  # fmt: skip
        if not evidence:
            claim.set_status(ClaimStatus.UNVERIFIED, CheckCode.NO_EVIDENCE.value, SetBy.ANALYSE)
        claims.append(claim)
    return Opportunity(
        id=oid, run_id=c.run_id, owner_id=c.owner_id, batch=batch_no, ord=ord_, kind=OppKind(d.kind),
        title=d.title, one_line=d.one_line, created_at=now,
        cluster_ids=tuple(clusters[k].id for k in d.cluster_ids if k in clusters),
        readiness_checklist=[x.model_dump() for x in d.readiness_checklist], effort_estimate=d.effort_estimate,
        next_steps=list(d.next_steps), analyst_version=version, model=model, claims=claims,
    )  # fmt: skip


async def _analyse_call(c: StepContext, data: GapContext) -> AgentOutcome[GapAnalysis]:
    return await c.agents.analyse(data, c.call_ctx("discovery.analyse"))


async def analyse(c: StepContext) -> Summary:
    """GapAnalyst over batches of candidate clusters; drafts and claims are persisted unchecked."""
    run = await c.read()
    step = RunStep.ANALYSE
    async with c.uow() as uow:
        clusters = await uow.clusters.list(c.run_id)
        probes = await uow.probes.list(c.run_id)
        corpus = await uow.corpus.list(c.run_id)
        previous = await uow.opportunities.previous_titles(c.owner_id, c.run_id)
        drafts_so_far = await uow.opportunities.list_for_run(c.run_id)
    measured = {p.cluster_id for p in probes if p.status == "measured"}
    cands = sorted(
        (cl for cl in clusters if cl.candidate_rank is not None and cl.id in measured),
        key=lambda cl: (cl.candidate_rank or 0, cl.key),
    )
    size = c.cfg.candidates_per_batch
    batches = [cands[i : i + size] for i in range(0, len(cands), size)]
    material = [m for m in await c.material(run) if m.active]
    by_key = {cl.label_key: cl for cl in clusters}
    origins = {w.openalex_id: w.origin for w in corpus}
    done: list[int] = list(run.step_state[step].data.get("batches_done", []))
    exclude = [t for _, t in previous] + [o.title for o in drafts_so_far]
    truncated = len(batches) > c.cfg.analyse_max_calls
    made = len(drafts_so_far)
    for n, batch in enumerate(batches[: c.cfg.analyse_max_calls]):
        if n in done:
            continue
        data = _analyst_context(c, run, batch, probes, corpus, material, exclude)
        try:
            outcome = await c.ask("reasoning", partial(_analyse_call, c, data))
        except BudgetExhausted:
            truncated = True
            break
        now = c.clock.now()
        opps = [
            _to_opportunity(c, d, n, k, by_key, origins, outcome.prompt_version, outcome.model, now)
            for k, d in enumerate(outcome.output.opportunities)
        ]

        async def write(
            uow: DiscoveryUnitOfWork,
            r: AnalysisRun,
            opps: list[Opportunity] = opps,
            n: int = n,
            outcome: AgentOutcome[GapAnalysis] = outcome,
            now: datetime = now,
        ) -> None:
            await uow.opportunities.add_drafts(opps)
            st = r.step_state[step]
            st.data["batches_done"] = [*st.data.get("batches_done", []), n]
            r.prompt_versions["GapAnalyst"] = outcome.prompt_version
            r.record_progress(step, c.generation, len(st.data["batches_done"]), len(batches), now)

        await c.tx(write)
        made += len(opps)
        exclude.extend(o.title for o in opps)
    if truncated:
        await c.tx(lambda uow, r: _degrade(r, c, step, "analyse_truncated"))
    return {"batches": len(batches), "drafts": made}


# ---- 7. verify -----------------------------------------------------------------------------------------------------
def _ref_of(e: Evidence) -> EvidenceRef:
    kind = (
        "count"
        if e.source_type is SourceType.COUNT
        else "work"
        if e.source_type in (SourceType.LITERATURE, SourceType.PROFILE)
        else ("insight" if e.insight_ref else "chunk")
    )
    return EvidenceRef(kind, e.label, e.quote)  # type: ignore[arg-type]


def _build_index(
    c: StepContext, corpus: list[CorpusWork], material: list[DocMaterial], probes: list[NoveltyProbe]
) -> RunMaterialIndex:
    works = {w.openalex_id: WorkMaterial(w.openalex_id, w.title, w.abstract, w.origin) for w in corpus}
    chunks: dict[str, ChunkMaterial] = {}
    insights: dict[str, InsightMaterial] = {}
    for m in material:
        for p in m.passages:
            chunks[p.label] = ChunkMaterial(
                p.label, p.chunk_id, c.owner_id, m.active, m.retention, p.text, tuple(p.quotes)
            )
        for i in m.insights:
            insights[i.ref] = InsightMaterial(i.ref, i.text, tuple(i.chunk_labels))
    pm = {p.label: ProbeMaterial(p.label, p.query, p.count, p.status == "measured") for p in probes}
    return RunMaterialIndex(c.owner_id, works, chunks, insights, pm)


def _apply_result(
    c: StepContext, e: Evidence, res: CheckResult, index: RunMaterialIndex, doc_ids: dict[str, UUID]
) -> None:
    """Record one check on the evidence and, when it passed, the ids of the object it resolved to."""
    if not res.ok:
        e.mark_failed(res.code, res.detail)
        return
    if e.source_type in (SourceType.LITERATURE, SourceType.PROFILE):
        w = index.works[e.label]
        e.source_type = SourceType.PROFILE if w.origin is CorpusOrigin.PROFILE else SourceType.LITERATURE
        e.corpus_work_id, e.ref = corpus_work_id(c.run_id, e.label), e.label
    elif e.source_type is SourceType.COUNT:
        e.probe_id, e.ref = probe_id(c.run_id, e.label), e.label
    else:
        label = e.label
        if e.insight_ref:
            labels = index.insights[e.label].chunk_labels
            label = next((x for x in labels if x in index.chunks), "")
        chunk = index.chunks.get(label)
        if chunk is None:
            e.mark_failed(CheckCode.UNKNOWN_INSIGHT, "no passage")
            return
        e.chunk_id, e.ref = chunk.chunk_id, str(chunk.chunk_id)
        e.document_id = doc_ids.get(_doc_label(label))
    e.mark_verified(res.detail)


def _evidence_text(e: Evidence, index: RunMaterialIndex) -> EvidenceText | None:
    if e.verified is not True:
        return None
    if e.source_type in (SourceType.LITERATURE, SourceType.PROFILE):
        w = index.works[e.label]
        return EvidenceText(
            ref_id=e.label, type="work", title=w.title[:200], text=(w.abstract or w.title)[:1200]
        )
    if e.source_type is SourceType.COUNT:
        p = index.probes[e.label]
        return EvidenceText(ref_id=e.label, type="count", text=f"{p.query}: {p.count} works"[:1200])
    if e.insight_ref:
        return EvidenceText(ref_id=e.label, type="insight", text=index.insights[e.label].text[:1200])
    ch = index.chunks[e.label]
    return EvidenceText(ref_id=e.label, type="chunk", text=(ch.text or "\n".join(ch.quotes))[:1200])


async def verify(c: StepContext) -> Summary:
    """Deterministic checks on every reference, then one Critic call per surviving draft."""
    run = await c.read()
    step = RunStep.VERIFY
    async with c.uow() as uow:
        corpus = await uow.corpus.list(c.run_id)
        probes = await uow.probes.list(c.run_id)
        everything = await uow.opportunities.list_for_run(c.run_id)
    total_drafts = len(everything)
    opps = [o for o in everything if o.verify_state == "pending"]
    all_docs = await c.material(run)
    material = [m for m in all_docs if m.active]
    inactive = {m.label for m in all_docs if not m.active}
    doc_ids = {d.label: d.document_id for d in run.inputs.documents}
    index = _build_index(c, corpus, material, probes)
    works = cast(dict[str, WorkMaterial], index.works)
    resolved = 0
    resolved_new = 0
    budget_out: str | None = None
    counts: Counter[str] = Counter()

    for opp in opps:
        new_works: list[CorpusWork] = []
        results: dict[UUID, list[tuple[EvidenceRef, CheckResult]]] = {}
        for claim in opp.claims:
            if claim.status is not ClaimStatus.PENDING:
                continue
            rows: list[tuple[EvidenceRef, CheckResult]] = []
            for e in claim.evidence:
                ref = _ref_of(e)
                if ref.type == "work" and ref.id not in works and resolved < c.cfg.max_resolve:
                    resolved += 1
                    try:
                        rec = await c.literature.get_work(ref.id)
                    except BudgetExhausted:
                        rec = None
                    if rec is not None:
                        cw = to_corpus(c.run_id, rec, CorpusOrigin.RESOLVED)
                        new_works.append(cw)
                        resolved_new += 1
                        works[rec.id] = WorkMaterial(rec.id, cw.title, cw.abstract, CorpusOrigin.RESOLVED)
                if ref.type in ("chunk", "insight") and _doc_label(ref.id) in inactive:
                    res = CheckResult(CheckCode.SOURCE_DELETED)
                else:
                    try:
                        res = check_ref(ref, index)
                    except InvariantViolation:
                        log.error("discovery.foreign_chunk", run_id=str(c.run_id))
                        res = CheckResult(CheckCode.UNKNOWN_CHUNK, "foreign")
                _apply_result(c, e, res, index, doc_ids)
                rows.append((ref, res))
            results[claim.id] = rows
            code = check_claim(claim.text, rows, index)
            if not claim_passes(code):
                claim.set_status(ClaimStatus.UNVERIFIED, code.value, SetBy.VERIFY_CODE)
        alive = [cl for cl in opp.claims if cl.status is ClaimStatus.PENDING]
        if EMAIL.search(" ".join([opp.title, opp.one_line, *opp.next_steps])):
            for cl in alive:
                cl.set_status(ClaimStatus.UNVERIFIED, CheckCode.PERSONAL_DATA.value, SetBy.VERIFY_CODE)
            _finish_opp(opp, OppStatus.UNVERIFIED, CheckCode.PERSONAL_DATA.value)
            counts["personal_data"] += 1
        elif not alive:
            _finish_opp(opp, OppStatus.UNVERIFIED, "no_verified_claims")
            counts["no_verified_claims"] += 1
        else:
            outcome = None
            if budget_out is None:
                try:
                    outcome = await _critic(c, opp, alive, index, corpus)
                except BudgetExhausted:
                    budget_out = "budget_exhausted"
                except _LLM_ERRORS:
                    if not c.last_attempt:
                        raise
                    budget_out = "critic_unavailable"
            if outcome is None:
                for cl in alive:
                    cl.set_status(ClaimStatus.UNVERIFIED, budget_out, SetBy.VERIFY_CODE)
                _finish_opp(opp, OppStatus.UNVERIFIED, budget_out or "critic_unavailable")
                counts[budget_out or "critic_unavailable"] += 1
            else:
                _apply_verdict(opp, alive, outcome.output, outcome.prompt_version, outcome.model)
                counts[opp.status.value if opp.status is not OppStatus.DRAFT else "draft"] += 1
        opp.verify_state = "done"

        async def write(
            uow: DiscoveryUnitOfWork,
            r: AnalysisRun,
            opp: Opportunity = opp,
            new_works: list[CorpusWork] = new_works,
        ) -> None:
            if new_works:
                await uow.corpus.upsert_many(new_works)
            await uow.opportunities.save(opp)
            if opp.critic_version:
                r.prompt_versions["Critic"] = opp.critic_version
            st = r.step_state[step]
            r.record_progress(step, c.generation, st.done + 1, len(opps), c.clock.now())

        await c.tx(write)
    if budget_out:
        await c.tx(lambda uow, r: _degrade(r, c, step, f"verify_{budget_out}"))
    by_status = Counter(f"n_{o.status.value}" for o in everything)
    done_resolved = sum(1 for w in corpus if w.origin is CorpusOrigin.RESOLVED) + resolved_new
    return {"checked": total_drafts, "resolved": done_resolved, **by_status}


def _finish_opp(opp: Opportunity, status: OppStatus, reason: str) -> None:
    opp.status, opp.status_reason, opp.status_step = status, reason, "verify"


async def _critic(
    c: StepContext, opp: Opportunity, alive: list[Claim], index: RunMaterialIndex, corpus: list[CorpusWork]
) -> AgentOutcome[CriticVerdict]:
    texts: dict[str, EvidenceText] = {}
    for cl in opp.claims:
        for e in cl.evidence:
            t = _evidence_text(e, index)
            if t:
                texts[e.label] = t
    mine = title_tokens(f"{opp.title} {opp.one_line}")
    similar = sorted(
        (w for w in corpus if w.abstract is not None or w.title),
        key=lambda w: (-jaccard(mine, title_tokens(w.title)), w.openalex_id),
    )[:5]
    data = CriticInput(
        draft=CriticDraft(
            title=opp.title, kind=opp.kind.value,
            claims=[
                CriticClaim(
                    index=cl.ord, text=cl.text,
                    check_status="passed" if cl.status is ClaimStatus.PENDING else (cl.status_reason or cl.status.value),
                    evidence=[e.label for e in cl.evidence],
                )
                for cl in opp.claims
            ],
        ),
        evidence_texts=list(texts.values()),
        similar_papers=[SimilarPaper(work_id=w.openalex_id, title=w.title[:200], year=w.year, abstract=(w.abstract or "")[:800]) for w in similar],
    )  # fmt: skip
    return await c.ask("reasoning", lambda: c.agents.critique(data, c.call_ctx("discovery.critic")))


def _apply_verdict(opp: Opportunity, alive: list[Claim], v: CriticVerdict, version: str, model: str) -> None:
    by_index: dict[int, ClaimVerdict] = {x.index: x for x in v.claims}
    outcomes: dict[int, str] = {}
    for cl in alive:
        verdict = by_index.get(cl.ord)
        cl.agent, cl.prompt_version, cl.model = "Critic", version, model
        if verdict is None:
            cl.set_status(ClaimStatus.UNVERIFIED, CheckCode.CRITIC_NO_VERDICT.value, SetBy.VERIFY_CRITIC)
            outcomes[cl.ord] = "unverified"
            continue
        cl.critic_verdict, cl.critic_reason, cl.critic_fix = verdict.status, verdict.reason, verdict.fix
        outcomes[cl.ord] = verdict.status
    verified = [cl for cl in alive if outcomes[cl.ord] == "verified"]
    contradicted = any(s == "contradicted" for s in outcomes.values())
    literature = any(
        e.source_type is SourceType.LITERATURE for cl in verified for e in cl.verified_evidence()
    )
    keep = v.keep and not contradicted and len(verified) >= 2 and literature and not v.already_done
    for cl in alive:
        s = outcomes[cl.ord]
        if cl.status is not ClaimStatus.PENDING:
            continue
        if s == "verified":
            cl.set_status(
                ClaimStatus.REJECTED_BY_CRITIC if not keep else ClaimStatus.VERIFIED,
                cl.critic_reason if not keep else None,
                SetBy.VERIFY_CRITIC,
            )
        elif s == "contradicted":
            cl.set_status(ClaimStatus.CONTRADICTED, cl.critic_reason, SetBy.VERIFY_CRITIC)
        else:
            cl.set_status(ClaimStatus.UNVERIFIED, cl.critic_reason, SetBy.VERIFY_CRITIC)
    opp.critic_version, opp.model = version, model
    if not keep:
        _finish_opp(
            opp, OppStatus.REJECTED_BY_CRITIC, "already_done" if v.already_done else "critic_rejected"
        )


# ---- 8. score_persist ----------------------------------------------------------------------------------------------
async def score_persist(c: StepContext) -> Summary:
    """Score, add the measured-novelty claim, drop duplicates, apply the show rule, publish the survivors."""
    run = await c.read()
    step = RunStep.SCORE_PERSIST
    async with c.uow() as uow:
        clusters = {cl.id: cl for cl in await uow.clusters.list(c.run_id)}
        probes = {p.id: p for p in await uow.probes.list(c.run_id)}
        opps = await uow.opportunities.list_for_run(c.run_id)
        previous = await uow.opportunities.previous_titles(c.owner_id, c.run_id)
    weights = {t.id: t.weight for t in run.inputs.topics}
    since = run.inputs.novelty_from or (c.clock.now().year - c.field.novelty.years_back)
    sat = c.field.novelty.saturation_count
    ready = [o for o in opps if o.status is OppStatus.DRAFT and o.verify_state == "done"]
    scored: list[Opportunity] = []
    for o in ready:
        probe = _novelty_probe(o, clusters, probes)
        if probe is None or probe.count is None:
            o.status, o.status_reason, o.status_step = (
                OppStatus.UNVERIFIED,
                "no_novelty_measure",
                "score_persist",
            )
            continue
        count = probe.count
        topic_ids = [t for cid in o.cluster_ids if cid in clusters for t in clusters[cid].topic_ids][:10]
        insight = sum(clusters[cid].insight_weight for cid in o.cluster_ids if cid in clusters)
        checklist = o.readiness_checklist
        fit = fit_score(weights, topic_ids, insight)
        nov = novelty_score(count, sat)
        rdy = readiness_score(sum(1 for x in checklist if x.get("present_in_vault")), len(checklist))
        eff = effort_value(o.effort_estimate)
        o.scores = Scores(fit, nov, rdy, eff, rank_score(fit, nov, rdy, eff))
        o.novelty_probe_id = probe.id
        o.score_basis = {
            "count": count, "probe": probe.label, "query": probe.query, "since_year": since, "saturation": sat,
            "band": c.field.novelty.bands.label(count),
        }  # fmt: skip
        if not any(cl.section is ClaimSection.SCORE for cl in o.claims):
            ordn = max((cl.ord for cl in o.claims), default=-1) + 1
            cid = claim_id(o.id, ordn)
            ev = Evidence(
                id=evidence_id(cid, 0),
                ord=0,
                source_type=SourceType.COUNT,
                label=probe.label,
                ref=probe.label,
                probe_id=probe.id,
            )
            ev.mark_verified(f"count={count}")
            sc = Claim(
                id=cid,
                ord=ordn,
                section=ClaimSection.SCORE,
                text=f"{count} works since {since} match '{probe.query}'"[:300],
                evidence=[ev],
            )
            sc.set_status(ClaimStatus.VERIFIED, None, SetBy.SCORE_PERSIST)
            o.claims.append(sc)
        scored.append(o)

    scored.sort(key=lambda o: (-(o.scores.rank if o.scores else 0.0), o.batch, o.ord))
    kept_titles: list[str] = []
    kept_ids: list[UUID] = []
    prev_titles = [t for _, t in previous]
    shown = 0
    for o in scored:
        dup = is_duplicate(o.title, [*prev_titles, *kept_titles])
        if dup is not None:
            o.status, o.status_reason, o.status_step = OppStatus.DUPLICATE, "duplicate", "score_persist"
            o.duplicate_of = previous[dup][0] if dup < len(previous) else kept_ids[dup - len(previous)]
            continue
        ok, reason = o.show_decision()
        if ok and shown < c.cfg.max_shown:
            o.status, o.status_reason, o.status_step = OppStatus.SHOWN, None, "score_persist"
            shown += 1
            kept_titles.append(o.title)
            kept_ids.append(o.id)
        else:
            o.status, o.status_reason, o.status_step = (
                OppStatus.UNVERIFIED,
                reason if not ok else "rank_cutoff",
                "score_persist",
            )

    async def write(uow: DiscoveryUnitOfWork, r: AnalysisRun) -> None:
        for o in opps:
            await uow.opportunities.save(o)
        r.record_progress(step, c.generation, 1, 1, c.clock.now())

    await c.tx(write)
    by_status = Counter(o.status.value for o in opps)
    shown = by_status[OppStatus.SHOWN.value]
    return {"shown": shown, "opportunities": len(opps), **{f"n_{k}": v for k, v in by_status.items()}}


def _novelty_probe(
    o: Opportunity, clusters: dict[UUID, Cluster], probes: dict[UUID, NoveltyProbe]
) -> NoveltyProbe | None:
    """The probe behind the opportunity's novelty: its cited count when verified, else its clusters' smallest count."""
    cited = [
        probes[e.probe_id]
        for cl in o.claims
        if cl.status is ClaimStatus.VERIFIED
        for e in cl.verified_evidence()
        if e.probe_id in probes
    ]
    pool = [p for p in cited if p.count is not None]
    if not pool:
        pool = [
            p
            for p in probes.values()
            if p.cluster_id in o.cluster_ids and p.status == "measured" and p.count is not None
        ]
    return min(pool, key=lambda p: (cast(int, p.count), p.label)) if pool else None
