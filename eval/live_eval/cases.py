"""Eval cases: recorded published inputs and synthetic sample inputs, each with an ideal output.

* Published-class agents (``ClusterLabeler``, ``MatchExplainer``) run on recorded inputs (public OpenAlex data from
  ``backend/tests/fixtures/llm/*/*.input.json``) and on variants built from public work titles.
* Confidential-class agents run ONLY on synthetic inputs (``synthetic=True``, text labelled ``[SAMPLE]``). The runner
  refuses a confidential-class case that is not synthetic, so a recorded vault text can never reach a model.
* ``ideal`` is what a perfect model returns; the scripted offline backend replays it and the checks use ``meta``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from live_eval.samples import (
    FACTS,
    ROOT,
    SAMPLE_CHUNKS,
    SAMPLE_INSIGHTS,
    SAMPLE_TAG,
    first_sentence,
    public_works,
)
from scout.discovery.domain.agent_io import (
    ChunkForAnalyst,
    CoverageRow,
    CriticClaim,
    CriticDraft,
    CriticInput,
    EvidenceText,
    GapContext,
    InsightForAnalyst,
    InsightTopicTaggerInput,
    NoveltyRow,
    PaperForAnalyst,
    PlanField,
    PlanInsight,
    PlanTopic,
    ProfileSummary,
    QueryPlannerInput,
    RecentWork,
    SimilarPaper,
    TagCluster,
    TagInsight,
)
from scout.matching.domain.agent_io import (
    JointIdeasInput,
    MatchExplainerInput,
    MethodExtractorInput,
    MethodSource,
    ResearcherSummary,
    ScoreBreakdown,
    SignalItem,
    SummaryWork,
    VocabularyEntry,
)

PUBLISHED_AGENTS = ("ClusterLabeler", "MatchExplainer")
CONFIDENTIAL_AGENTS = (
    "GapAnalyst", "Critic", "QueryPlanner", "MethodExtractor", "InsightExtractor", "InsightTopicTagger", "JointIdeas",
)  # fmt: skip
ALL_AGENTS = (*PUBLISHED_AGENTS, *CONFIDENTIAL_AGENTS)
LLM_FIXTURES = ROOT / "backend" / "tests" / "fixtures" / "llm"


@dataclass(frozen=True)
class EvalCase:
    """One agent call with its ideal answer and the facts the rule checks need."""

    id: str
    agent: str
    data_class: str  # "published" | "confidential" (the agent's class, from its prompt)
    input: Any  # AgentInput or a plain mapping
    ideal: dict[str, Any]
    synthetic: bool = False
    label: str = "plain"  # "good" | "bad" | "plain"
    meta: dict[str, Any] = field(default_factory=dict)


def _recorded(agent: str, stem: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (LLM_FIXTURES / agent / f"{stem}.input.json").read_text(encoding="utf-8")
    )
    return data


def _dump(model: Any) -> dict[str, Any]:
    out: dict[str, Any] = model.model_dump(mode="json")
    return out


# ---- published-class agents ---------------------------------------------------------------------------------------
def cluster_labeler_cases() -> list[EvalCase]:
    """The recorded ClusterLabeler inputs (public OpenAlex topics and titles)."""
    from scout.discovery.domain.agent_io import ClusterLabelInput

    out = []
    for path in sorted((LLM_FIXTURES / "ClusterLabeler").glob("*.input.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        ideal = json.loads(path.with_name(path.name.replace(".input", "")).read_text(encoding="utf-8"))
        inp = ClusterLabelInput.model_validate(data)
        out.append(
            EvalCase(
                f"cluster-{path.name.split('.')[0]}",
                "ClusterLabeler",
                "published",
                inp,
                ideal,
                meta={"cluster_id": inp.cluster_id, "titles": list(inp.sample_titles)},
            )  # fmt: skip
        )
    return out


def _summary_variant(
    base: ResearcherSummary, works: list[tuple[str, str, int | None]], n_methods: int
) -> ResearcherSummary:
    return ResearcherSummary(
        topics=list(base.topics), methods=list(base.methods[:n_methods]),
        works=[SummaryWork(ref=f"work:{w}", title=t[:200]) for w, t, _ in works],
    )  # fmt: skip


def _pair_variants(n: int) -> list[tuple[ResearcherSummary, ResearcherSummary, ScoreBreakdown]]:
    """``n`` deterministic pairs: Foster-like side A and Moreira-like side B with public work titles, some swapped."""
    base = MatchExplainerInput.model_validate(_recorded("MatchExplainer", "7b52d7d912867fc1"))
    foster, moreira = public_works("A5059588273", 24), public_works("A5033675494", 24)
    out = []
    for i in range(n):
        k = 3 + i % 3
        fa = foster[i : i + 4] if len(foster) >= i + 4 else foster[:4]
        mb = moreira[i : i + 4] if len(moreira) >= i + 4 else moreira[:4]
        a = _summary_variant(base.a, fa, k)
        b = _summary_variant(base.b, mb, 2 + i % 2)
        bd = ScoreBreakdown(
            shared_topics=base.score_breakdown.shared_topics[: 2 + i % 3],
            a_only_methods=[m.label for m in a.methods], b_only_methods=[m.label for m in b.methods],
            score=base.score_breakdown.score,
        )  # fmt: skip
        if i % 2:
            a, b = b, a
            bd = bd.model_copy(
                update={"a_only_methods": bd.b_only_methods, "b_only_methods": bd.a_only_methods}
            )
        out.append((a, b, bd))
    return out


def _perspective(you: ResearcherSummary, they: ResearcherSummary, shared: list[str]) -> dict[str, Any]:
    def items(s: ResearcherSummary) -> list[dict[str, Any]]:
        labels = [m.label.lower() for m in s.methods[:2]] or ["their field"]
        return [{"text": f"Experience with {x}", "source_refs": []} for x in labels]

    topic = shared[0] if shared else "related topics"
    lead = you.methods[0].label.lower() if you.methods else "your methods"
    other = they.methods[0].label.lower() if they.methods else "their methods"
    why = f"You both work on {topic.lower()}. Your {lead} combined with their {other} could open a joint question."
    return {"you_bring": items(you), "they_bring": items(they), "why": why}


def match_explainer_cases(n: int = 12) -> list[EvalCase]:
    """``n`` anonymity cases: works carry public titles, so a title copied into the text is a detectable leak."""
    out = []
    for i, (a, b, bd) in enumerate(_pair_variants(n)):
        inp = MatchExplainerInput(a=a, b=b, score_breakdown=bd)
        ideal = {"for_a": _perspective(a, b, bd.shared_topics), "for_b": _perspective(b, a, bd.shared_topics)}
        titles = [w.title for s in (a, b) for w in s.works]
        ids = [t.id for s in (a, b) for t in s.topics] + [m.id for s in (a, b) for m in s.methods]
        out.append(
            EvalCase(
                f"match-{i:02d}",
                "MatchExplainer",
                "published",
                inp,
                ideal,
                meta={
                    "titles": titles,
                    "ids": ids,
                    "refs_a": [w.ref for w in a.works],
                    "refs_b": [w.ref for w in b.works],
                },
            )  # fmt: skip
        )
    return out


# ---- synthetic confidential-class agents --------------------------------------------------------------------------
def critic_cases() -> list[EvalCase]:
    """8 good drafts (all claims supported) and 8 planted-bad drafts (one claim the evidence does not support)."""

    def ev(f: Any) -> EvidenceText:
        return EvidenceText(ref_id=f.work_id, type="work", title=f.title, year=2023, text=f.abstract)

    count = EvidenceText(ref_id="n1", type="count", text="lignin foam fire resistance: 412 works since 2021")
    cases: list[EvalCase] = []
    for i, fa in enumerate(FACTS):
        fb = FACTS[(i + 1) % len(FACTS)]
        sim = FACTS[(i + 4) % len(FACTS)]
        claims = [
            CriticClaim(index=0, text=fa.good, check_status="passed", evidence=[fa.work_id]),
            CriticClaim(index=1, text=fb.good, check_status="passed", evidence=[fb.work_id]),
        ]
        texts = [ev(fa), ev(fb)]
        if i % 2 == 0:
            claims.append(
                CriticClaim(
                    index=2,
                    text="Only 412 works since 2021 match 'lignin foam fire resistance'.",
                    check_status="passed",
                    evidence=["n1"],
                )
            )
            texts.append(count)
        sims = [SimilarPaper(work_id=sim.work_id, title=sim.title, year=2022, abstract=sim.abstract)]
        draft = CriticDraft(title=f"Sample draft {i}: {fa.title[:60]}", kind="synergy", claims=claims)
        ideal = {
            "claims": [{"index": c.index, "status": "verified", "reason": None, "fix": None} for c in claims],
            "already_done": False, "already_done_by": None, "keep": True, "reasons": ["Every claim is supported by its evidence"],
        }  # fmt: skip
        inp = CriticInput(draft=draft, evidence_texts=texts, similar_papers=sims)
        cases.append(EvalCase(f"critic-good-{i}", "Critic", "confidential", inp, ideal, True, "good",
                              {"indices": [c.index for c in claims], "work_ids": [t.ref_id for t in texts] + [sim.work_id]}))  # fmt: skip
    for i, fa in enumerate(FACTS):
        fg = FACTS[(i + 3) % len(FACTS)]
        claims = [
            CriticClaim(index=0, text=fg.good, check_status="passed", evidence=[fg.work_id]),
            CriticClaim(index=1, text=fa.bad, check_status="passed", evidence=[fa.work_id]),
        ]
        draft = CriticDraft(title=f"Sample draft bad {i}: {fa.title[:55]}", kind="white_spot", claims=claims)
        status = "contradicted" if fa.bad_kind == "contradicted" else "unverified"
        ideal = {
            "claims": [
                {"index": 0, "status": "verified", "reason": None, "fix": None},
                {"index": 1, "status": status, "reason": f"Evidence does not support the claim ({fa.bad_kind})", "fix": fa.good},
            ],
            "already_done": False, "already_done_by": None, "keep": False, "reasons": ["One claim is not supported"],
        }  # fmt: skip
        sims = [
            SimilarPaper(
                work_id=FACTS[(i + 5) % len(FACTS)].work_id,
                title=FACTS[(i + 5) % len(FACTS)].title,
                year=2022,
            )
        ]
        inp = CriticInput(draft=draft, evidence_texts=[ev(fg), ev(fa)], similar_papers=sims)
        cases.append(EvalCase(f"critic-bad-{i}", "Critic", "confidential", inp, ideal, True, "bad",
                              {"indices": [0, 1], "planted_index": 1, "bad_kind": fa.bad_kind,
                               "work_ids": [fg.work_id, fa.work_id, sims[0].work_id]}))  # fmt: skip
    # alternate good and bad so a small --max-calls still samples both
    good = [c for c in cases if c.label == "good"]
    bad = [c for c in cases if c.label == "bad"]
    return [c for pair in zip(good, bad, strict=True) for c in pair]


def gap_analyst_cases() -> list[EvalCase]:
    """3 synthetic gap-analysis inputs: public profile titles, SAMPLE abstracts, SAMPLE lab notes."""
    foster, moreira = public_works("A5059588273", 6), public_works("A5033675494", 6)
    base = MatchExplainerInput.model_validate(_recorded("MatchExplainer", "7b52d7d912867fc1"))
    out = []
    for k in range(3):
        works = moreira if k == 1 else foster
        fa, fb = FACTS[k], FACTS[k + 3]
        chunk_text = SAMPLE_CHUNKS[k % 2]
        side = base.b if k == 1 else base.a
        ctx = GapContext(
            profile_summary=ProfileSummary(
                topics=[t.name for t in side.topics[:6]],
                recent_works=[RecentWork(work_id=w, title=t[:200], year=y) for w, t, y in works[:4]],
            ),
            insights=[InsightForAnalyst(insight_ref=f"d1.i{n}", document="sample-notes.pdf", section="aims", text=f"{SAMPLE_TAG} {s}") for n, s in enumerate(SAMPLE_INSIGHTS)],
            coverage=[CoverageRow(cluster_id="k1", label="Lignin foams", corpus_count=420, user_works=3, user_insights=0.5, density=0.2)],
            novelty=[NoveltyRow(candidate_id="n1", cluster_id="k1", query="lignin foam fire resistance", since_year=2021, count=412)],
            papers=[PaperForAnalyst(work_id=f.work_id, title=f.title, year=2023, abstract=f.abstract) for f in (fa, fb, FACTS[(k + 5) % 8])],
            chunks=[ChunkForAnalyst(chunk_id="d1.c0", document="sample-notes.pdf", page=1, text=chunk_text)],
            max_opportunities=2,
        )  # fmt: skip
        notes_quote = chunk_text.split(". ")[1]
        ideal = {"opportunities": [{
            "kind": "white_spot", "title": f"Sample opportunity {k}: {fa.title[:70]}", "one_line": "Sample one-line summary.",
            "cluster_ids": ["k1"],
            "claims": [
                {"text": fa.good, "section": "rationale", "evidence_refs": [{"type": "work", "id": fa.work_id, "quote": first_sentence(fa.abstract)}]},
                {"text": "Your own notes already plan the matching experiment.", "section": "rationale", "evidence_refs": [{"type": "chunk", "id": "d1.c0", "quote": notes_quote}]},
                {"text": "Only 412 works since 2021 match the query, a small literature.", "section": "rationale", "evidence_refs": [{"type": "count", "id": "n1", "quote": None}]},
            ],
            "readiness_checklist": [{"item": "Characterise the lignin batches", "present_in_vault": False, "ref": None}],
            "effort_estimate": "medium", "next_steps": ["Run a small pilot"],
        }]}  # fmt: skip
        meta = {
            "work_text": {p.work_id: f"{p.title} {p.abstract}" for p in ctx.papers} | {w.work_id: w.title for w in ctx.profile_summary.recent_works},
            "chunk_text": {c.chunk_id: c.text for c in ctx.chunks},
            "insight_ids": [i.insight_ref for i in ctx.insights], "count_ids": [n.candidate_id for n in ctx.novelty],
            "cluster_ids": [c.cluster_id for c in ctx.coverage], "max_opportunities": ctx.max_opportunities,
            "profile_ids": [w.work_id for w in ctx.profile_summary.recent_works],
        }  # fmt: skip
        out.append(EvalCase(f"gap-{k}", "GapAnalyst", "confidential", ctx, ideal, True, meta=meta))
    return out


def query_planner_cases() -> list[EvalCase]:
    """2 synthetic planner inputs."""
    base = MatchExplainerInput.model_validate(_recorded("MatchExplainer", "7b52d7d912867fc1"))
    out = []
    for k, side in enumerate((base.a, base.b)):
        topics = [PlanTopic(id=t.id, name=t.name, weight=t.weight) for t in side.topics[:5]]
        inp = QueryPlannerInput(
            topics=topics,
            recent_titles=[t[:200] for _, t, _ in public_works("A5033675494" if k else "A5059588273", 3)],
            insights=[PlanInsight(insight_ref=f"d1.i{n}", section="aims", text=f"{SAMPLE_TAG} {s}") for n, s in enumerate(SAMPLE_INSIGHTS)],
            keywords=["lignin foam", "membrane fouling"],
            field=PlanField(name="Materials science (sample)", openalex_topic_ids=[t.id for t in topics[:2]], keywords=["materials"]),
            year_from=2021,
        )  # fmt: skip
        purposes = ["core", "adjacent", "objection", "bridge"]
        ideal = {"queries": [{"search": f"{(topics[j % len(topics)].name or "topic").lower()} {w}", "purpose": purposes[j], "because": "Sample reason", "max_results": 20} for j, w in enumerate(["review", "methods", "limitations", "hybrid"])]}  # fmt: skip
        out.append(EvalCase(f"planner-{k}", "QueryPlanner", "confidential", inp, ideal, True))
    return out


def method_extractor_cases() -> list[EvalCase]:
    """2 synthetic extraction inputs over the shared method vocabulary."""
    base = MatchExplainerInput.model_validate(_recorded("MatchExplainer", "7b52d7d912867fc1"))
    vocab = [VocabularyEntry(method_id=m.id, label=m.label) for s in (base.a, base.b) for m in s.methods]
    specs = [
        ([("work:W9000000101", "We isolated nanocellulose by TEMPO oxidation and made foams from lignin-based polyols."), ("insight:i1", "Lignin fractionation by solvent extraction precedes the foaming trials.")], ["nanocellulose_isolation", "foaming", "lignin_fractionation"]),
        ([("work:W9000000102", "A photocatalytic TiO2 film was prepared and a machine learning model predicted its activity."), ("insight:i2", "Polymer synthesis of the binder was repeated in three batches.")], ["photocatalysis", "machine_learning", "polymer_synthesis"]),
    ]  # fmt: skip
    out = []
    for k, (sources, expected) in enumerate(specs):
        inp = MethodExtractorInput(
            vocabulary=vocab,
            sources=[MethodSource(source_ref=r, text=f"{SAMPLE_TAG} {t}") for r, t in sources],
        )
        ideal = {"methods": [{"method_id": e, "new_label": None, "source_refs": [sources[min(j, 1)][0]], "confidence": "high"} for j, e in enumerate(expected)]}  # fmt: skip
        out.append(EvalCase(f"methods-{k}", "MethodExtractor", "confidential", inp, ideal, True,
                            meta={"vocab": [v.method_id for v in vocab], "refs": [r for r, _ in sources], "expected": expected}))  # fmt: skip
    return out


def tagger_cases() -> list[EvalCase]:
    """2 synthetic topic-tagging inputs."""
    clusters = [TagCluster(cluster_id="k1", label="Lignin foams", description="Lignin polyols in foams"), TagCluster(cluster_id="k2", label="Membrane filtration", description="Nanocellulose membranes"), TagCluster(cluster_id="k3", label="Photocatalysis", description="TiO2 films for pollutants")]  # fmt: skip
    out = []
    for k in range(2):
        insights = [TagInsight(insight_ref="d1.i0", text=f"{SAMPLE_TAG} {SAMPLE_INSIGHTS[0]}"), TagInsight(insight_ref="d1.i1", text=f"{SAMPLE_TAG} {SAMPLE_INSIGHTS[1]}")]  # fmt: skip
        ideal = {"tags": [{"insight_ref": "d1.i0", "cluster_ids": ["k1"], "strength": "core", "reason": "Lignin foams"}, {"insight_ref": "d1.i1", "cluster_ids": ["k2"], "strength": "core", "reason": "Membrane flux"}]}  # fmt: skip
        inp = InsightTopicTaggerInput(clusters=clusters[: 3 - k], insights=insights)
        out.append(EvalCase(f"tagger-{k}", "InsightTopicTagger", "confidential", inp, ideal, True,
                            meta={"cluster_ids": [c.cluster_id for c in inp.clusters], "refs": [i.insight_ref for i in insights]}))  # fmt: skip
    return out


def insight_extractor_cases() -> list[EvalCase]:
    """2 synthetic documents (plain mapping input: the real input class is private to the vault adapter)."""
    out = []
    objection = "The sample size of three lignin batches is too small to support the proposed correlation"
    for k in range(2):
        chunks = [
            {"chunk_id": "c0", "page": 1, "text": SAMPLE_CHUNKS[k]},
            {
                "chunk_id": "c1",
                "page": 2,
                "text": f"{SAMPLE_TAG} Reviewer comment. {objection} with foam cell structure.",
            },
        ]
        inp = {
            "mode": "full",
            "filename": f"sample-notes-{k}.pdf",
            "kind_hint": None,
            "chunks": chunks,
            "partials": [],
        }
        ideal = {
            "kind": "lab_notes", "kind_reason": "Dated lab observations", "title": f"Sample notes {k}",
            "aims": [{"summary": "Relate material properties to structure", "chunk_ids": ["c0"]}], "findings": [],
            "methods": [], "datasets": [],
            "reviewer_objections": [{"quote": objection, "summary": "Sample size too small", "chunk_ids": ["c1"]}],
            "open_questions": [{"summary": "Which effect causes the change?", "chunk_ids": ["c0"]}], "keywords": ["lignin", "membrane"],
        }  # fmt: skip
        out.append(EvalCase(f"insight-{k}", "InsightExtractor", "confidential", inp, ideal, True,
                            meta={"chunk_text": {c["chunk_id"]: c["text"] for c in chunks}}))  # fmt: skip
    return out


def joint_ideas_cases(n: int = 4) -> list[EvalCase]:
    """``n`` synthetic joint-idea inputs: public titles on both sides, SAMPLE signals that must not be copied."""
    out = []
    for i, (a, b, bd) in enumerate(_pair_variants(n)):
        sig_a = [
            SignalItem(
                ref="insight:s1",
                text=f"{SAMPLE_TAG} We measured a flux drop of 15 percent after 6 hours in the membrane trial and could not explain it.",
            )
        ]
        sig_b = [
            SignalItem(
                ref="insight:s2",
                text=f"{SAMPLE_TAG} Our TiO2 film lost 12 percent of its activity after the third reuse cycle in the sample reactor.",
            )
        ]
        inp = JointIdeasInput(a=a, b=b, a_signals=sig_a, b_signals=sig_b, shared_topics=bd.shared_topics, a_only_methods=bd.a_only_methods, b_only_methods=bd.b_only_methods)  # fmt: skip
        ra, rb = (a.works[0].ref if a.works else "insight:s1"), (b.works[0].ref if b.works else "insight:s2")
        ideal = {"ideas": [{
            "title": "Joint screening of a shared problem",
            "question": "Can the methods of one side explain the loss of performance seen on the other side?",
            "uses_from_a": [{"text": f"Experience with {(bd.a_only_methods or ['their methods'])[0].lower()}", "source_refs": [ra]}],
            "uses_from_b": [{"text": f"Experience with {(bd.b_only_methods or ['their methods'])[0].lower()}", "source_refs": [rb]}],
            "first_step": "Exchange two representative samples and compare results in a short call.",
            "possible_output": "Short joint proposal",
        }]}  # fmt: skip
        meta = {
            "titles": [w.title for s in (a, b) for w in s.works], "signals": [s.text for s in (*sig_a, *sig_b)],
            "refs_a": [w.ref for w in a.works] + [s.ref for s in sig_a], "refs_b": [w.ref for w in b.works] + [s.ref for s in sig_b],
        }  # fmt: skip
        out.append(EvalCase(f"joint-{i}", "JointIdeas", "confidential", inp, ideal, True, meta=meta))
    return out


def build_cases(agents: list[str] | None = None) -> list[EvalCase]:
    """All cases (optionally for some agents), interleaved round-robin by agent so a small call cap samples every agent."""
    by_agent: dict[str, list[EvalCase]] = {
        "ClusterLabeler": cluster_labeler_cases(), "MatchExplainer": match_explainer_cases(),
        "GapAnalyst": gap_analyst_cases(), "Critic": critic_cases(), "QueryPlanner": query_planner_cases(),
        "MethodExtractor": method_extractor_cases(), "InsightExtractor": insight_extractor_cases(),
        "InsightTopicTagger": tagger_cases(), "JointIdeas": joint_ideas_cases(),
    }  # fmt: skip
    chosen = [a for a in ALL_AGENTS if agents is None or a in agents]
    queues = [list(by_agent[a]) for a in chosen]
    out: list[EvalCase] = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out
