"""Pipeline steps 1-4: plan_queries, retrieve, group_by_topic, coverage (DECISIONS 63-64)."""

from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from collections.abc import Coroutine
from dataclasses import replace
from functools import partial
from uuid import UUID

import structlog

from scout.discovery.application.pipeline import (
    StepContext,
    insight_refs,
    to_corpus,
    with_inputs,
)
from scout.discovery.domain.agent_io import (
    ClusterLabelInput,
    InsightTopicTaggerInput,
    InsightTopicTags,
    PlanField,
    PlanInsight,
    PlanTopic,
    QueryPlannerInput,
    TagCluster,
    TagInsight,
    TopicName,
)
from scout.discovery.domain.budget import BudgetExhausted, OpenAlexCapReached
from scout.discovery.domain.corpus import Cluster, CorpusWork
from scout.discovery.domain.coverage import (
    ClusterStats,
    candidate_scores,
    density,
    pick_candidates,
    tag_weight,
    user_coverage,
)
from scout.discovery.domain.enums import CorpusOrigin, RunFailure, RunStep
from scout.discovery.domain.grouping import ClusterDraft, GroupInput, group_works
from scout.discovery.domain.ids import cluster_id, corpus_work_id
from scout.discovery.domain.run import AnalysisRun, RunFailed, TopicRef
from scout.discovery.ports.agents import AgentOutcome
from scout.discovery.ports.repos import DiscoveryUnitOfWork
from scout.ports.literature import WorkQuery, WorkRecord
from scout.ports.llm import LLMOutputInvalid, LLMUnavailable
from scout.shared.text import clean_text, norm

log = structlog.get_logger()
Summary = dict[str, int | str]
_LLM_ERRORS = (LLMUnavailable, LLMOutputInvalid)


# ---- 1. plan_queries -----------------------------------------------------------------------------------------------
async def plan_queries(c: StepContext) -> Summary:
    """One QueryPlanner call; profile works enter the corpus; the code validates the queries."""
    run = await c.read()
    step = RunStep.PLAN_QUERIES
    if not c.profile.import_done:
        raise RunFailed(RunFailure.PROFILE_MISSING)
    material = await c.material(run)
    active = [m for m in material if m.active]
    now_year = c.clock.now().year
    year_from, novelty_from = now_year - c.field.query.years_back, now_year - c.field.novelty.years_back
    keywords = list(dict.fromkeys(k for m in active for k in m.keywords))[:30]
    existing = run.step_state.get(step)
    queries = existing.data.get("queries") if existing else None
    version: str | None = None
    if not queries:
        data = QueryPlannerInput(
            topics=[PlanTopic(id=t[0], name=t[1], weight=t[3]) for t in c.profile.topics[:20]],
            recent_titles=[clean_text(w.title, 200) for w in c.profile.works[:20]],
            insights=[
                PlanInsight(insight_ref=i.ref, section=i.section, text=i.text)
                for m in active
                for i in m.insights
            ][:60],
            keywords=keywords,
            field=PlanField(
                name=c.field.label, openalex_topic_ids=c.field.openalex_topic_ids, keywords=c.field.keywords
            ),
            year_from=year_from,
        )
        try:
            outcome = await c.ask(
                "fast", lambda: c.agents.plan_queries(data, c.call_ctx("discovery.plan_queries"))
            )
        except BudgetExhausted as exc:
            raise RunFailed(RunFailure.BUDGET_EXHAUSTED) from exc
        version = outcome.prompt_version
        seen: set[str] = set()
        queries = []
        for q in outcome.output.queries:
            key = norm(q.search).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            queries.append(
                {
                    "label": f"q{len(queries) + 1}",
                    "search": clean_text(q.search, 80),
                    "purpose": q.purpose,
                    "because": q.because,
                    "max_results": min(q.max_results, 200),
                }
            )
            if len(queries) >= c.cfg.max_queries:
                break
    profile_works = [
        CorpusWork(
            id=corpus_work_id(c.run_id, pw.openalex_id), run_id=c.run_id, openalex_id=pw.openalex_id, title=clean_text(pw.title, 500),
            abstract=clean_text(pw.abstract, 4000) if pw.abstract else None, year=pw.year, cited_by=pw.cited_by,
            type=pw.type, doi=pw.doi.lower() if pw.doi else None,
            topics=tuple({"id": t[0], "name": t[1], "subfield": t[2]} for t in pw.topics),
            primary_topic_id=pw.topics[0][0] if pw.topics else None, authors=(), origin=CorpusOrigin.PROFILE,
        )
        for pw in c.profile.works[: c.cfg.max_profile_works]
    ]  # fmt: skip

    async def write(uow: DiscoveryUnitOfWork, r: AnalysisRun) -> None:
        await uow.corpus.upsert_many(profile_works)
        r.inputs = with_inputs(
            r,
            researcher_id=c.profile.researcher_id,
            topics=tuple(_topic_ref(t) for t in c.profile.topics),
            insight_refs=insight_refs(material),
            basis=c.basis(),
            year_from=year_from,
            novelty_from=novelty_from,
        )
        rec = r.step_state[step]
        rec.data["queries"] = queries
        if version:
            r.prompt_versions["QueryPlanner"] = version

    await c.tx(write)
    return {"queries": len(queries), "profile_works": len(profile_works)}


def _topic_ref(t: tuple[str, str | None, str | None, float]) -> TopicRef:
    return TopicRef(id=t[0], name=t[1], subfield=t[2], weight=t[3])


# ---- 2. retrieve ----------------------------------------------------------------------------------------------------
async def retrieve(c: StepContext) -> Summary:
    """Run the planned searches in waves; fill the corpus up to the cap; a cap hit degrades, an empty corpus fails."""
    run = await c.read()
    step = RunStep.RETRIEVE
    queries: list[dict[str, object]] = list(run.step_state[RunStep.PLAN_QUERIES].data["queries"])
    rec = run.step_state.get(step)
    done_labels: list[str] = list(rec.data.get("done", [])) if rec else []
    pending = [q for q in queries if q["label"] not in done_labels]
    year_from = run.inputs.year_from or (c.clock.now().year - c.field.query.years_back)
    cap_hit = False
    wave = c.cfg.openalex_concurrency

    async def one(q: dict[str, object]) -> list[WorkRecord]:
        async with c._oa_sem:
            return list(
                await c.literature.search_works(
                    WorkQuery(
                        search=str(q["search"]),
                        from_publication_date=f"{year_from}-01-01",
                        type=c.field.query.type,
                        per_page=int(q["max_results"]),  # type: ignore[call-overload]
                    )
                )
            )

    for i in range(0, len(pending), wave):
        batch = pending[i : i + wave]
        results = await asyncio.gather(*(one(q) for q in batch), return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        for e in errors:
            if not isinstance(e, OpenAlexCapReached | Exception):
                raise e
        hard = [e for e in errors if not isinstance(e, OpenAlexCapReached)]
        cap_hit = cap_hit or len(errors) > len(hard)
        fresh = [(q, r) for q, r in zip(batch, results, strict=True) if not isinstance(r, BaseException)]

        async def write(
            uow: DiscoveryUnitOfWork,
            r: AnalysisRun,
            fresh: list[tuple[dict[str, object], list[WorkRecord]]] = fresh,
        ) -> None:
            known = await uow.corpus.list(c.run_id)
            ids = {w.openalex_id for w in known}
            dois = {w.doi for w in known if w.doi}
            total = sum(1 for w in known if w.origin is CorpusOrigin.SEARCH)
            add: list[CorpusWork] = []
            for q, recs in fresh:
                for wr in recs:
                    cw = to_corpus(c.run_id, wr, CorpusOrigin.SEARCH, str(q["label"]))
                    if wr.id in ids:
                        add.append(cw)  # only appends the query label
                        continue
                    if (cw.doi and cw.doi in dois) or total >= r.budget.max_corpus:
                        continue
                    ids.add(wr.id)
                    if cw.doi:
                        dois.add(cw.doi)
                    total += 1
                    add.append(cw)
            await uow.corpus.upsert_many(add)
            st = r.step_state[step]
            st.data["done"] = [*st.data.get("done", []), *(str(q["label"]) for q, _ in fresh)]
            r.record_progress(step, c.generation, len(st.data["done"]), len(queries), c.clock.now())

        if fresh:
            await c.tx(write)
        if hard:
            raise hard[0]
        if cap_hit:
            break
    if cap_hit:
        await c.tx(lambda uow, r: _degrade(r, c, step, "openalex_cap"))
    async with c.uow() as uow:
        n = sum(1 for w in await uow.corpus.list(c.run_id) if w.origin is CorpusOrigin.SEARCH)
    if n == 0:
        raise RunFailed(RunFailure.CORPUS_EMPTY)
    return {"works": n, "queries_done": len(done_labels) + len(pending)}


async def _degrade(run: AnalysisRun, c: StepContext, step: RunStep, reason: str) -> None:
    if not any(d.step is step and d.reason == reason for d in run.degraded):
        run.degrade(step, reason, c.generation)


async def _gather_or_cancel(coros: list[Coroutine[None, None, None]]) -> None:
    """Run together; when one fails, cancel the others and wait for them, so nothing keeps calling after a cancel."""
    tasks = [asyncio.ensure_future(x) for x in coros]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


# ---- 3. group_by_topic ---------------------------------------------------------------------------------------------
def _topic_names(works: list[CorpusWork]) -> list[tuple[str, str]]:
    names: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for w in works:
        for t in w.topics:
            if t.get("id") and t.get("name"):
                names[t["id"]] = str(t["name"])
                counts[t["id"]] += 1
    return [(i, names[i]) for i, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _fallback(draft: ClusterDraft, members: list[CorpusWork]) -> tuple[str, list[str]]:
    if draft.is_misc:
        return "Other topics", []
    names = _topic_names(members)
    label = names[0][1] if names else (draft.subfield or draft.key)
    return clean_text(label, 60), [clean_text(n, 30) for _, n in names[:2]]


async def group_by_topic(c: StepContext) -> Summary:
    """Deterministic clusters first (topic-named), then up to ``max_labelled`` LLM labels, one commit each."""
    step = RunStep.GROUP_BY_TOPIC
    async with c.uow() as uow:
        works = await uow.corpus.list(c.run_id, CorpusOrigin.SEARCH)
        existing = {cl.id: cl for cl in await uow.clusters.list(c.run_id)}
    by_id = {w.openalex_id: w for w in works}
    drafts = group_works(
        [
            GroupInput(
                w.openalex_id, tuple(str(t["id"]) for t in w.topics),
                str(w.topics[0].get("subfield")) if w.topics and w.topics[0].get("subfield") else None,
            )
            for w in works
        ],
        c.field.clusters.min_size,
        c.field.clusters.max_size,
    )  # fmt: skip
    clusters: list[Cluster] = []
    assignments: dict[str, UUID] = {}
    for d in drafts:
        cid = cluster_id(c.run_id, d.key)
        members = [by_id[i] for i in d.work_ids]
        for i in d.work_ids:
            assignments[i] = cid
        old = existing.get(cid)
        if old is not None and old.label_source == "llm":
            old.size, old.label_key, old.topic_ids = d.size, d.label_key, list(d.topic_ids)
            clusters.append(old)
            continue
        label, terms = _fallback(d, members)
        clusters.append(
            Cluster(
                id=cid,
                run_id=c.run_id,
                key=d.key,
                label_key=d.label_key,
                label=label,
                description="",
                search_terms=terms,
                label_source="topic",
                topic_ids=list(d.topic_ids),
                subfield=d.subfield,
                size=d.size,
                is_misc=d.is_misc,
            )  # fmt: skip
        )

    async def first(uow: DiscoveryUnitOfWork, r: AnalysisRun) -> None:
        await uow.clusters.upsert_many(clusters)
        await uow.corpus.set_clusters(c.run_id, assignments)
        labelled = sum(1 for cl in clusters if cl.label_source == "llm")
        total = min(len(clusters), c.field.clusters.max_labelled)
        r.record_progress(step, c.generation, min(labelled, total), total, c.clock.now())

    await c.tx(first)

    todo = [cl for cl in clusters if not cl.is_misc and cl.label_source == "topic"][
        : c.field.clusters.max_labelled
    ]
    members_of = {d.key: [by_id[i] for i in d.work_ids] for d in drafts}
    degraded = False

    async def label_one(cl: Cluster) -> None:
        nonlocal degraded
        ms = sorted(members_of[cl.key], key=lambda w: (-w.cited_by, w.openalex_id))
        data = ClusterLabelInput(
            cluster_id=cl.label_key,
            openalex_topics=[TopicName(id=i, name=n) for i, n in _topic_names(members_of[cl.key])[:10]],
            sample_titles=[w.title[:200] for w in ms[:10]],
        )
        try:
            outcome = await c.ask("fast", lambda: c.agents.label_cluster(data, c.call_ctx("discovery.label")))
        except BudgetExhausted:
            degraded = True
            return
        except _LLM_ERRORS:
            degraded = True
            return
        out = outcome.output

        async def write(uow: DiscoveryUnitOfWork, r: AnalysisRun) -> None:
            cl.label, cl.description = out.label, out.description
            if out.search_terms:
                cl.search_terms = list(out.search_terms)
            cl.label_source = "llm"
            await uow.clusters.upsert_many([cl])
            r.prompt_versions["ClusterLabeler"] = outcome.prompt_version
            st = r.step_state[step]
            r.record_progress(step, c.generation, min(st.done + 1, st.total), st.total, c.clock.now())

        await c.tx(write)

    await _gather_or_cancel([label_one(cl) for cl in todo])
    real = [cl for cl in clusters if not cl.is_misc]
    if degraded or any(cl.label_source == "topic" for cl in real):
        await c.tx(lambda uow, r: _degrade(r, c, step, "label_fallback"))
    return {"clusters": len(clusters), "labelled": sum(1 for cl in real if cl.label_source == "llm")}


async def _tag_call(c: StepContext, data: InsightTopicTaggerInput) -> AgentOutcome[InsightTopicTags]:
    return await c.agents.tag_insights(data, c.call_ctx("discovery.tag"))


# ---- 4. coverage ---------------------------------------------------------------------------------------------------
async def coverage(c: StepContext) -> Summary:
    """Where the researcher already works: published works and vault insights per cluster; pick candidates."""
    run = await c.read()
    step = RunStep.COVERAGE
    async with c.uow() as uow:
        clusters = await uow.clusters.list(c.run_id)
        profile_works = await uow.corpus.list(c.run_id, CorpusOrigin.PROFILE)
    material = [m for m in await c.material(run) if m.active]
    insights = [i for m in material for i in m.insights]
    by_topic: dict[str, Cluster] = {}
    for cl in sorted(clusters, key=lambda x: x.label_key):
        for t in cl.topic_ids:
            by_topic.setdefault(t, cl)
    user_works: Counter[UUID] = Counter()
    for w in profile_works:
        hit = next((by_topic[str(t["id"])] for t in w.topics if str(t["id"]) in by_topic), None)
        if hit is not None:
            user_works[hit.id] += 1

    rec = run.step_state.get(step)
    tags: list[list[str]] | None = rec.data.get("tags") if rec else None
    degrade_tags = False
    tagger_version: str | None = None
    if tags is None:
        tags = []
        real = [cl for cl in clusters if not cl.is_misc][:30]
        if insights and real:
            known = {cl.label_key for cl in real}
            wanted = {i.ref for i in insights}
            for k in range(0, len(insights), 40):
                chunk = insights[k : k + 40]
                data = InsightTopicTaggerInput(
                    clusters=[
                        TagCluster(cluster_id=cl.label_key, label=cl.label, description=cl.description)
                        for cl in real
                    ],
                    insights=[TagInsight(insight_ref=i.ref, text=i.text) for i in chunk],
                )
                try:
                    outcome = await c.ask("fast", partial(_tag_call, c, data))
                except (BudgetExhausted, *_LLM_ERRORS):
                    degrade_tags = True
                    break
                tagger_version = outcome.prompt_version
                for tag in outcome.output.tags:
                    if tag.insight_ref in wanted:
                        tags.extend(
                            [tag.insight_ref, cid, tag.strength] for cid in tag.cluster_ids if cid in known
                        )
    weights: dict[UUID, float] = defaultdict(float)
    by_key = {cl.label_key: cl for cl in clusters}
    for _ref, cid, strength in tags:
        weights[by_key[cid].id] += tag_weight(strength)
    largest = max((cl.size for cl in clusters if not cl.is_misc), default=0)
    stats = [
        ClusterStats(cl.key, cl.size, cl.subfield, user_works[cl.id], weights[cl.id], cl.is_misc)
        for cl in clusters
    ]
    picked, fallback = pick_candidates(stats)
    scores = candidate_scores(stats)
    ranks = {k: n for n, k in enumerate(picked, start=1)}
    updated: list[Cluster] = []
    for cl in clusters:
        s = next(x for x in stats if x.key == cl.key)
        updated.append(
            replace(
                cl,
                user_works=s.user_works,
                insight_weight=s.insight_weight,
                user_coverage=user_coverage(s.user_works, s.insight_weight),
                density=density(cl.size, largest),
                candidate_rank=ranks.get(cl.key),
            )  # fmt: skip
        )

    async def write(uow: DiscoveryUnitOfWork, r: AnalysisRun) -> None:
        await uow.clusters.upsert_many(updated)
        st = r.step_state[step]
        st.data["tags"] = tags
        if tagger_version:
            r.prompt_versions["InsightTopicTagger"] = tagger_version
        st.data["candidates"] = picked
        if degrade_tags:
            await _degrade(r, c, step, "tags_skipped")
        if fallback:
            await _degrade(r, c, step, "no_adjacent_clusters")

    await c.tx(write)
    _ = scores
    return {"candidates": len(picked), "insights": len(insights), "tags": len(tags)}
