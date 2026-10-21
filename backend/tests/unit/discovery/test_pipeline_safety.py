"""Safety of the run pipeline: last attempts, deleted sources, hostile drafts and private text (plan M3 tasks 3-5;
design M3 sections 1 (show rule, ``visible_claims``), 2.1 (errors), 2.2 verify, 3 (vault content) and 8 (canary)).

* Last attempt: a transient model error raises and keeps the run ``running`` for the retry; on the last attempt the
  run fails with a plain code. In ``verify`` the last attempt does not fail the run (``critic_unavailable``).
* A document deleted mid-run: its evidence becomes ``source_deleted`` and what depended on it is not shown.
* Hostile drafts: whatever the model writes is checked by code. Only fully verified claims reach a user; the rest is
  persisted (never deleted) with a reason.
* Canary: private vault text and model output never reach ``run.error_code``, job results or logs.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID

import pytest
from structlog.testing import capture_logs
from tests.unit.discovery._world import (
    ABSTRACTS,
    DOC_ID,
    OWNER,
    TITLES,
    World,
    after_step,
    analyst,
    assert_user_view_clean,
    call_names,
    chunk_ref,
    claim,
    count_ref,
    draft,
    good_claims,
    insight_ref,
    plan,
    row_ids,
    script_drafts,
    src_bug,
    work,
    work_ref,
)

from scout.discovery.application.pipeline import PipelineConfig
from scout.discovery.domain.agent_io import (
    ClaimDraft,
    ClaimVerdict,
    CriticInput,
    CriticVerdict,
    OpportunityDraft,
)
from scout.discovery.domain.enums import (
    CheckCode,
    ClaimSection,
    ClaimStatus,
    CorpusOrigin,
    OppStatus,
    RunStatus,
    RunStep,
    SetBy,
    SourceType,
)
from scout.discovery.domain.ids import corpus_work_id
from scout.discovery.domain.opportunity import Opportunity
from scout.discovery.domain.run import AnalysisRun
from scout.discovery.ports.sources import DocMaterial, PassageView
from scout.ports.literature import WorkRecord
from scout.ports.llm import LLMOutputInvalid, LLMUnavailable
from scout.shared import InfrastructureError

# ---- last attempt -----------------------------------------------------------------------------------------------------
Install = Callable[[World, Exception], None]


def raising(agent: str) -> Install:
    def install(w: World, exc: Exception) -> None:
        def boom(_: Any) -> Any:
            raise exc

        w.agents.script(agent, boom)

    return install


def failing_literature(kind: str) -> Install:
    def install(w: World, exc: Exception) -> None:
        w.literature.fail[kind] = exc

    return install


def repair(w: World) -> None:
    """Undo every installed failure (the outage is over)."""
    w.agents.scripts.pop("QueryPlanner", None)
    w.agents.script("QueryPlanner", plan)
    w.literature.fail.clear()
    w.agents.script("GapAnalyst", analyst)


FAILURES = {
    "planner-llm-down": (raising("QueryPlanner"), LLMUnavailable("route down"), RunStep.PLAN_QUERIES, "llm_unavailable"),
    "planner-bad-output": (raising("QueryPlanner"), LLMOutputInvalid("bad json"), RunStep.PLAN_QUERIES, "llm_unavailable"),
    "search-down": (failing_literature("search"), InfrastructureError("openalex 503"), RunStep.RETRIEVE, "literature_unavailable"),
    "count-down": (failing_literature("count"), InfrastructureError("openalex 503"), RunStep.MEASURE_NOVELTY, "literature_unavailable"),
    "analyst-llm-down": (raising("GapAnalyst"), LLMUnavailable("route down"), RunStep.ANALYSE, "llm_unavailable"),
    "analyst-bad-output": (raising("GapAnalyst"), LLMOutputInvalid("bad json"), RunStep.ANALYSE, "llm_unavailable"),
    "analyst-bug": (raising("GapAnalyst"), RuntimeError("boom"), RunStep.ANALYSE, "internal_error"),
}  # fmt: skip


@pytest.mark.parametrize("case", list(FAILURES))
async def test_a_transient_error_raises_and_keeps_the_run_running_for_the_retry(case: str) -> None:
    install, exc, step, _ = FAILURES[case]
    w = World()
    install(w, exc)
    run = await w.start()
    with pytest.raises(type(exc)):
        await w.run(run, last=False)
    stored = w.get(run)
    assert stored.status is RunStatus.RUNNING and stored.step is step
    assert stored.error_code is None and stored.finished_at is None
    # the queue retries once the outage is over: the run resumes and finishes
    repair(w)
    assert (await w.run(run, last=False))["status"] == "done"
    assert w.get(run).generation == 2
    assert w.agents.count("QueryPlanner") <= 2  # at most the failed attempt and the retry


@pytest.mark.parametrize("case", list(FAILURES))
async def test_on_the_last_attempt_the_run_fails_with_a_plain_code_and_the_error_is_still_raised(
    case: str,
) -> None:
    install, exc, step, code = FAILURES[case]
    w = World()
    install(w, exc)
    run = await w.start()
    with pytest.raises(type(exc)):
        await w.run(run, last=True)
    stored = w.get(run)
    assert stored.status is RunStatus.FAILED and stored.error_code == code and stored.error_step is step
    assert stored.finished_at is not None and stored.finished_at is not None
    assert str(exc) not in repr(stored)  # the error text is not stored on the run
    assert not w.shown(run)


async def test_a_failed_run_is_final_a_later_job_attempt_skips_it() -> None:
    w = World()
    raising("GapAnalyst")(w, LLMUnavailable("down"))
    run = await w.start()
    with pytest.raises(LLMUnavailable):
        await w.run(run, last=True)
    assert await w.run(run) == {"skipped": "failed"}


@pytest.mark.parametrize("exc", [LLMUnavailable("down"), LLMOutputInvalid("bad")])
@pytest.mark.parametrize("last", [False, True])
async def test_a_model_failure_while_labelling_clusters_degrades_instead_of_failing(
    exc: Exception, last: bool
) -> None:
    w = World()
    raising("ClusterLabeler")(w, exc)
    run = await w.start()
    result = await w.run(run, last=last)
    stored = w.get(run)
    assert result["status"] == "done" and stored.partial
    assert (RunStep.GROUP_BY_TOPIC, "label_fallback") in {(d.step, d.reason) for d in stored.degraded}
    assert all(c.label_source == "topic" for c in w.store.clusters[run.id].values())


@pytest.mark.parametrize("last", [False, True])
async def test_a_model_failure_in_the_tagger_degrades_instead_of_failing(last: bool) -> None:
    w = World()
    raising("InsightTopicTagger")(w, LLMUnavailable("down"))
    run = await w.start()
    result = await w.run(run, last=last)
    assert result["status"] == "done"
    assert (RunStep.COVERAGE, "tags_skipped") in {(d.step, d.reason) for d in w.get(run).degraded}


async def test_in_verify_a_transient_critic_error_raises_before_the_last_attempt() -> None:
    w = World()
    raising("Critic")(w, LLMUnavailable("down"))
    run = await w.start()
    with pytest.raises(LLMUnavailable):
        await w.run(run, last=False)
    stored = w.get(run)
    assert stored.status is RunStatus.RUNNING and stored.step is RunStep.VERIFY and stored.error_code is None
    assert all(o.verify_state == "pending" for o in w.opps(run))  # nothing was concluded from the outage


@pytest.mark.parametrize("exc", [LLMUnavailable("down"), LLMOutputInvalid("bad")])
async def test_in_verify_the_last_attempt_does_not_fail_the_run(exc: Exception) -> None:
    w = World()
    raising("Critic")(w, exc)
    run = await w.start()
    result = await w.run(run, last=True)  # does not raise
    stored = w.get(run)
    assert result["status"] == "done" and stored.status is RunStatus.DONE and stored.partial
    assert any(d.step is RunStep.VERIFY and "critic_unavailable" in d.reason for d in stored.degraded)
    assert w.agents.count("Critic") == 1  # after the first outage the remaining drafts are not even tried
    opps = w.opps(run)
    assert opps and not w.shown(run)
    for o in opps:
        assert o.status is OppStatus.UNVERIFIED and o.status_reason == "critic_unavailable"
        assert o.claims and all(
            c.status is ClaimStatus.UNVERIFIED and c.status_reason == "critic_unavailable" for c in o.claims
        )
        assert o.verify_state == "done" and o.visible_claims() == []
    assert_user_view_clean(w.store)


async def test_in_verify_a_bug_on_the_last_attempt_still_fails_the_run() -> None:
    w = World()
    raising("Critic")(w, RuntimeError("boom"))
    run = await w.start()
    with pytest.raises(RuntimeError):
        await w.run(run, last=True)
    stored = w.get(run)
    assert stored.status is RunStatus.FAILED and stored.error_code == "internal_error"
    assert stored.error_step is RunStep.VERIFY


# ---- documents deleted mid-run ----------------------------------------------------------------------------------------
def vault_dependent_draft(second: ClaimDraft) -> OpportunityDraft:
    """Exactly two supports: a corpus quote and a vault citation. Without the vault one the draft cannot be shown."""
    return draft(TITLES[0], [claim("The corpus work reports the key effect.", work_ref()), second])


CITES = {
    "passage-quote": claim("The note states the aim.", chunk_ref()),
    "insight": claim("The note states the aim.", insight_ref()),
}


@pytest.mark.parametrize("cite", list(CITES))
async def test_without_a_deletion_the_vault_dependent_draft_is_shown(cite: str) -> None:
    w = World()
    script_drafts(w, vault_dependent_draft(CITES[cite]))
    run = await w.start()
    await w.run(run)
    [opp] = w.opps(run)
    assert opp.status is OppStatus.SHOWN
    kinds = {e.source_type for c in opp.visible_claims() for e in c.evidence}
    assert SourceType.VAULT in kinds and SourceType.LITERATURE in kinds


@pytest.mark.parametrize("cite", list(CITES))
@pytest.mark.parametrize("deleted_after", [RunStep.MEASURE_NOVELTY, RunStep.ANALYSE])
async def test_a_document_deleted_before_verify_turns_its_evidence_into_source_deleted(
    cite: str, deleted_after: RunStep
) -> None:
    w = World()
    script_drafts(w, vault_dependent_draft(CITES[cite]))
    run = await w.start()
    after_step(w, deleted_after, lambda stored: w.vault.delete(DOC_ID))
    result = await w.run(run)
    assert result["status"] == "done"
    [opp] = w.opps(run)
    assert opp.status is not OppStatus.SHOWN and opp.status in (
        OppStatus.REJECTED_BY_CRITIC,
        OppStatus.UNVERIFIED,
    )
    cited = opp.claims[1]
    [ev] = cited.evidence
    assert ev.verified is False and ev.check is CheckCode.SOURCE_DELETED
    assert ev.chunk_id is None  # it resolves to nothing any more
    assert cited.status is ClaimStatus.UNVERIFIED and cited.status_reason == "source_deleted"
    assert cited.set_by is SetBy.VERIFY_CODE
    assert opp.visible_claims() == [] or all("note" not in c.text for c in opp.visible_claims())
    assert len(opp.claims) >= 2, "claims are kept, never deleted"
    assert not w.shown(run)
    # the deleted document's text was not sent to the Critic
    critic_inputs = [d for a, d in w.agents.calls if a == "Critic"]
    assert all("insulation panels" not in d.model_dump_json() for d in critic_inputs)
    assert_user_view_clean(w.store)


async def test_deleting_a_document_does_not_hurt_a_draft_that_cites_only_the_corpus() -> None:
    w = World()
    script_drafts(w, draft(TITLES[0], good_claims()))
    run = await w.start()
    after_step(w, RunStep.ANALYSE, lambda stored: w.vault.delete(DOC_ID))
    await w.run(run)
    [opp] = w.opps(run)
    assert opp.status is OppStatus.SHOWN


# ---- hostile drafts ---------------------------------------------------------------------------------------------------
LONG_QUOTE = ABSTRACTS["W12"] + " " + "x" * 300
Critic = Callable[[Any], CriticVerdict]
Verdict = Literal["verified", "unverified", "contradicted"]


def critic_with(
    status: dict[int, Verdict] | None = None,
    *,
    keep: bool = True,
    already_done: bool = False,
    omit: frozenset[int] = frozenset(),
) -> Critic:
    def verdict(data: Any) -> CriticVerdict:
        return CriticVerdict(
            claims=[
                ClaimVerdict(
                    index=c.index,
                    status=(status or {}).get(c.index, "verified"),
                    reason=f"critic says {c.index}",
                )
                for c in data.draft.claims
                if c.index not in omit
            ],
            keep=keep,
            already_done=already_done,
        )

    return verdict


async def run_hostile(
    hostile: ClaimDraft | None, critic: Critic | None = None, *, setup: Callable[[World], None] | None = None
) -> tuple[World, AnalysisRun, Opportunity]:
    """One draft [good, good, hostile]; returns the finished world and the draft's opportunity."""
    w = World()
    if setup:
        setup(w)
    script_drafts(w, draft(TITLES[0], good_claims() + ([hostile] if hostile else [])))
    if critic:
        w.agents.script("Critic", critic)
    run = await w.start()
    await w.run(run)
    [opp] = w.opps(run)
    return w, w.get(run), opp


def assert_only_verified_claims_are_visible(opp: Opportunity, hostile_text: str) -> None:
    for c in opp.visible_claims():
        assert c.status is ClaimStatus.VERIFIED
        assert all(e.verified is True for e in c.evidence)
        assert c.text != hostile_text


@dataclasses.dataclass(frozen=True)
class Hostile:
    claim: ClaimDraft
    code: CheckCode
    set_by: SetBy = SetBy.VERIFY_CODE
    evidence_check: CheckCode | None = (
        None  # the check recorded on the first evidence, when it is the culprit
    )


CODE_CASES = {
    "hallucinated-work-id": Hostile(
        claim("A paper outside the corpus proves it.", work_ref("W999999", "Study 999999 reports that lignin foams win")),
        CheckCode.UNKNOWN_WORK, evidence_check=CheckCode.UNKNOWN_WORK,
    ),
    "stitched-quote": Hostile(
        claim("The paper says so.", work_ref("W12", "Study 12 reports that lignin than polystyrene panels.")),
        CheckCode.QUOTE_NOT_FOUND, evidence_check=CheckCode.QUOTE_NOT_FOUND,
    ),
    "altered-quote": Hostile(
        claim("The paper says the opposite.", work_ref("W12", "Study 12 reports that lignin based foams reach higher thermal conductivity than polystyrene panels.")),
        CheckCode.QUOTE_NOT_FOUND, evidence_check=CheckCode.QUOTE_NOT_FOUND,
    ),
    "quote-too-short": Hostile(
        claim("The paper says so.", work_ref("W12", "lignin foams")), CheckCode.QUOTE_TOO_SHORT,
        evidence_check=CheckCode.QUOTE_TOO_SHORT,
    ),
    "quote-too-long": Hostile(
        claim("The paper says so.", work_ref("W12", LONG_QUOTE)), CheckCode.QUOTE_TOO_LONG,
        evidence_check=CheckCode.QUOTE_TOO_LONG,
    ),
    "quote-missing": Hostile(
        claim("The paper says so.", work_ref("W12", None)), CheckCode.QUOTE_MISSING, evidence_check=CheckCode.QUOTE_MISSING
    ),
    "count-mismatch": Hostile(claim("About 4000 works already cover this.", count_ref()), CheckCode.COUNT_MISMATCH),
    "unknown-count": Hostile(
        claim("The volume is small.", count_ref("n99")), CheckCode.UNKNOWN_COUNT, evidence_check=CheckCode.UNKNOWN_COUNT
    ),
    "email-in-claim": Hostile(claim("Ask jane.doe@example.edu for the dataset.", work_ref()), CheckCode.PERSONAL_DATA),
    "chunk-of-another-document": Hostile(
        claim("A passage of another document.", chunk_ref("d2.c1")), CheckCode.UNKNOWN_CHUNK,
        evidence_check=CheckCode.UNKNOWN_CHUNK,
    ),
    "altered-chunk-quote": Hostile(
        claim("The note says so.", chunk_ref("d1.c1", "develop lignin foams for car panels")),
        CheckCode.QUOTE_NOT_FOUND, evidence_check=CheckCode.QUOTE_NOT_FOUND,
    ),
    "unknown-insight": Hostile(
        claim("The note has an aim.", insight_ref("d1.aims[9]")), CheckCode.UNKNOWN_INSIGHT,
        evidence_check=CheckCode.UNKNOWN_INSIGHT,
    ),
    "novelty-claim-without-count": Hostile(
        claim("Few studies address this question.", work_ref()), CheckCode.NOVELTY_WITHOUT_COUNT
    ),
    "claim-without-evidence": Hostile(
        claim("A bare assertion nobody can check."), CheckCode.NO_EVIDENCE, set_by=SetBy.ANALYSE
    ),
}  # fmt: skip


@pytest.mark.parametrize("name", list(CODE_CASES))
async def test_a_claim_that_fails_the_code_checks_is_persisted_unverified_and_never_visible(
    name: str,
) -> None:
    case = CODE_CASES[name]
    w, _run, opp = await run_hostile(case.claim)
    bad = opp.claims[2]
    assert bad.text == case.claim.text, "the hostile claim is kept, not deleted"
    assert (
        bad.status is ClaimStatus.UNVERIFIED
        and bad.status_reason == case.code.value
        and bad.set_by is case.set_by
    )
    if case.evidence_check:
        assert bad.evidence[0].verified is False and bad.evidence[0].check is case.evidence_check
    assert bad.verified_evidence() == [] or case.evidence_check is None
    assert_only_verified_claims_are_visible(opp, bad.text)
    # the two good claims still carry the opportunity, and the score claim is added
    assert opp.status is OppStatus.SHOWN
    assert [c.status for c in opp.claims[:2]] == [ClaimStatus.VERIFIED] * 2
    assert len(opp.claims) == 4 and opp.claims[3].section is ClaimSection.SCORE
    assert [c.text for c in opp.visible_claims()] == [
        opp.claims[0].text,
        opp.claims[1].text,
        opp.claims[3].text,
    ]
    assert_user_view_clean(w.store)


@pytest.mark.parametrize("name", [n for n, c in CODE_CASES.items() if c.set_by is SetBy.VERIFY_CODE])
async def test_the_critic_is_told_which_claim_failed_the_code_checks_and_cannot_rescue_it(name: str) -> None:
    case = CODE_CASES[name]
    w, _, opp = await run_hostile(case.claim)  # the default Critic marks every claim it is shown as verified
    [seen] = [d for a, d in w.agents.calls if a == "Critic"]
    assert isinstance(seen, CriticInput)
    assert [c.check_status for c in seen.draft.claims[:2]] == ["passed", "passed"]
    assert seen.draft.claims[2].check_status == case.code.value
    assert opp.claims[2].status is ClaimStatus.UNVERIFIED  # still unverified though the Critic said verified


async def test_a_claim_without_any_evidence_is_parked_before_the_critic_sees_it() -> None:
    w, _, opp = await run_hostile(CODE_CASES["claim-without-evidence"].claim)
    [seen] = [d for a, d in w.agents.calls if a == "Critic"]
    assert len(seen.draft.claims) == 3 and seen.draft.claims[2].check_status == "no_evidence"
    assert opp.claims[2].set_by is SetBy.ANALYSE


async def test_a_verified_claim_shows_only_its_verified_evidence() -> None:
    mixed = claim(
        "The corpus work and a phantom both say so.",
        work_ref(),
        work_ref("W999999", "Study 999999 says it all"),
    )
    w, _, opp = await run_hostile(mixed)
    bad = opp.claims[2]
    assert bad.status is ClaimStatus.VERIFIED, "one verified citation is enough for the claim"
    assert [e.verified for e in bad.evidence] == [True, False]  # the phantom is persisted as failed
    shown = next(c for c in opp.visible_claims() if c.text == mixed.text)
    assert [e.label for e in shown.evidence] == ["W12"]
    assert_user_view_clean(w.store)


async def test_an_accurate_count_in_the_text_passes() -> None:
    _, _, opp = await run_hostile(
        claim("About 124 works match the query.", count_ref())
    )  # probe count is 120
    assert opp.claims[2].status is ClaimStatus.VERIFIED


# ---- hostile critics --------------------------------------------------------------------------------------------------
GOOD_EXTRA = claim("Another corpus work adds detail.", work_ref("W11", ABSTRACTS["W11"][:40]))


async def test_a_claim_the_critic_calls_unverified_is_kept_but_never_shown() -> None:
    _, _, opp = await run_hostile(GOOD_EXTRA, critic_with({2: "unverified"}))
    bad = opp.claims[2]
    assert bad.status is ClaimStatus.UNVERIFIED and bad.set_by is SetBy.VERIFY_CRITIC
    assert bad.status_reason == "critic says 2" and bad.critic_verdict == "unverified"
    assert opp.status is OppStatus.SHOWN and bad.text not in [c.text for c in opp.visible_claims()]


async def test_a_contradicted_claim_rejects_the_whole_opportunity() -> None:
    w, run, opp = await run_hostile(GOOD_EXTRA, critic_with({2: "contradicted"}))
    assert opp.status is OppStatus.REJECTED_BY_CRITIC and opp.status_reason == "critic_rejected"
    assert opp.claims[2].status is ClaimStatus.CONTRADICTED
    assert [c.status for c in opp.claims[:2]] == [ClaimStatus.REJECTED_BY_CRITIC] * 2
    assert [c.critic_verdict for c in opp.claims[:2]] == ["verified", "verified"]  # the judgement is kept
    assert opp.visible_claims() == [] and not w.shown(run)


async def test_already_done_work_rejects_the_opportunity_even_when_the_critic_says_keep() -> None:
    w, run, opp = await run_hostile(GOOD_EXTRA, critic_with(already_done=True, keep=True))
    assert opp.status is OppStatus.REJECTED_BY_CRITIC and opp.status_reason == "already_done"
    assert all(c.status is ClaimStatus.REJECTED_BY_CRITIC for c in opp.claims)
    assert opp.visible_claims() == [] and not w.shown(run)


async def test_keep_false_rejects_the_opportunity() -> None:
    w, run, opp = await run_hostile(GOOD_EXTRA, critic_with(keep=False))
    assert opp.status is OppStatus.REJECTED_BY_CRITIC and opp.status_reason == "critic_rejected"
    assert opp.visible_claims() == [] and not w.shown(run)


async def test_a_claim_the_critic_forgot_is_unverified_with_critic_no_verdict() -> None:
    _, _, opp = await run_hostile(GOOD_EXTRA, critic_with(omit=frozenset({2})))
    bad = opp.claims[2]
    assert bad.status is ClaimStatus.UNVERIFIED and bad.status_reason == CheckCode.CRITIC_NO_VERDICT.value
    assert opp.status is OppStatus.SHOWN and bad.text not in [c.text for c in opp.visible_claims()]


async def test_a_critic_that_verifies_only_one_claim_cannot_make_an_opportunity() -> None:
    w, run, opp = await run_hostile(
        None, critic_with(omit=frozenset({1}))
    )  # [good, good]; verdict for claim 0 only
    assert opp.status is OppStatus.REJECTED_BY_CRITIC and not w.shown(run)
    assert (
        opp.claims[1].status_reason == CheckCode.CRITIC_NO_VERDICT.value
        or opp.claims[1].status is ClaimStatus.UNVERIFIED
    )


async def test_a_verdict_for_a_claim_index_that_does_not_exist_is_ignored() -> None:
    def odd(data: Any) -> CriticVerdict:
        return CriticVerdict(claims=[ClaimVerdict(index=7, status="verified")], keep=True)

    w, run, opp = await run_hostile(None, odd)
    assert all(
        c.status is ClaimStatus.UNVERIFIED and c.status_reason == "critic_no_verdict" for c in opp.claims
    )
    assert not w.shown(run)


# ---- resolving works outside the corpus (A3) -------------------------------------------------------------------------
def with_known_work(w: World) -> None:
    rec: WorkRecord = work(
        13, "T1", abstract="Study 13 reports that lignin foams resist fire at high temperature."
    )
    w.literature.known["W13"] = rec


RESOLVABLE = claim(
    "A resolvable paper outside the search results.", work_ref("W13", "lignin foams resist fire at high")
)


async def test_a_cited_work_outside_the_corpus_is_resolved_stored_and_then_verified() -> None:
    w, run, opp = await run_hostile(RESOLVABLE, setup=with_known_work)
    assert w.literature.calls.count("get") == 1
    ev = opp.claims[2].evidence[0]
    assert opp.claims[2].status is ClaimStatus.VERIFIED and ev.verified is True
    assert ev.source_type is SourceType.LITERATURE
    stored = w.store.corpus[run.id]["W13"]
    assert stored.origin is CorpusOrigin.RESOLVED and ev.corpus_work_id == corpus_work_id(run.id, "W13")
    assert ev.corpus_work_id in row_ids(w.store, run.id)["corpus"]
    assert run.spend.openalex_calls == len(w.literature.calls)  # the lookup is charged like any call


async def test_resolution_is_limited_per_run() -> None:
    w = World(cfg=PipelineConfig(max_resolve=0))
    with_known_work(w)
    script_drafts(w, draft(TITLES[0], [*good_claims(), RESOLVABLE]))
    run = await w.start()
    await w.run(run)
    [opp] = w.opps(run)
    assert "get" not in w.literature.calls
    assert opp.claims[2].status is ClaimStatus.UNVERIFIED and opp.claims[2].status_reason == "unknown_work"
    assert opp.status is OppStatus.SHOWN and opp.claims[2].text not in [c.text for c in opp.visible_claims()]


async def test_every_shown_literature_citation_is_in_the_runs_corpus() -> None:
    w = World()
    run = await w.start()
    await w.run(run)
    corpus_ids = row_ids(w.store, run.id)["corpus"]
    cited = [
        e for o in w.shown(run) for c in o.visible_claims() for e in c.evidence
        if e.source_type in (SourceType.LITERATURE, SourceType.PROFILE)
    ]  # fmt: skip
    assert cited and all(e.corpus_work_id in corpus_ids for e in cited)


# ---- discard documents: quotes are never stitched ---------------------------------------------------------------------
FIRST = "first retained quote about lignin foams"
SECOND = "second retained quote about insulation panels"


def with_discard_document(w: World) -> UUID:
    """A second document (label d2) whose full text no longer exists: only two verified quotes were retained."""
    doc_id, chunk_id = UUID(int=800), UUID(int=801)
    passage = PassageView("d2.c1", chunk_id, doc_id, 3, 3, "discard", None, quotes=(FIRST, SECOND))
    w.vault.add(OWNER, DocMaterial(doc_id, "d2", "scan.pdf", "discard", True, passages=(passage,)))
    return doc_id


async def run_with_discard_claim(quote: str) -> tuple[World, Opportunity]:
    w = World()
    doc_id = with_discard_document(w)
    script_drafts(
        w, draft(TITLES[0], [*good_claims(), claim("The scan says so.", chunk_ref("d2.c1", quote))])
    )
    run = await w.start((DOC_ID, doc_id))
    await w.run(run)
    [opp] = w.opps(run)
    return w, opp


async def test_a_quote_inside_one_retained_quote_of_a_discard_document_is_verified() -> None:
    w, opp = await run_with_discard_claim("retained quote about lignin")
    assert opp.claims[2].status is ClaimStatus.VERIFIED
    ev = opp.claims[2].evidence[0]
    assert ev.verified is True and ev.chunk_id == UUID(int=801) and ev.document_id == UUID(int=800)
    assert_user_view_clean(w.store)


async def test_a_quote_stitched_from_two_retained_quotes_is_not_verified() -> None:
    _w, opp = await run_with_discard_claim(f"{FIRST} {SECOND}")
    bad = opp.claims[2]
    assert bad.status is ClaimStatus.UNVERIFIED and bad.status_reason == "quote_not_found"
    assert bad.evidence[0].verified is False and bad.evidence[0].chunk_id is None
    assert bad.text not in [c.text for c in opp.visible_claims()]


async def test_text_a_discard_document_never_kept_is_not_a_valid_quote() -> None:
    _, opp = await run_with_discard_claim("the original paragraph that was thrown away")
    assert opp.claims[2].status_reason == "quote_not_found"


# ---- ranking and caps -------------------------------------------------------------------------------------------------
async def test_drafts_beyond_the_shown_cap_are_kept_unverified_with_rank_cutoff() -> None:
    w = World(cfg=PipelineConfig(max_shown=2))
    script_drafts(w, *(draft(TITLES[i], good_claims()) for i in range(1, 4)))
    run = await w.start()
    await w.run(run)
    opps = w.opps(run)
    assert len(opps) == 3 and len(w.shown(run)) == 2
    [cut] = [o for o in opps if o.status is not OppStatus.SHOWN]
    assert cut.status is OppStatus.UNVERIFIED and cut.status_reason == "rank_cutoff" and cut.claims
    assert_user_view_clean(w.store)


# ---- profile ------------------------------------------------------------------------------------------------------------
async def test_a_profile_that_disappears_before_the_job_runs_fails_the_run_without_a_call() -> None:
    w = World()
    run = await w.start()
    del w.profiles.profiles[OWNER]
    assert await w.run(run) == {"failed": "profile_missing"}
    stored = w.get(run)
    assert stored.status is RunStatus.FAILED and stored.error_code == "profile_missing"
    assert w.agents.calls == [] and w.literature.calls == []


async def test_a_profile_whose_import_is_unfinished_fails_the_first_step() -> None:
    w = World()
    run = await w.start()
    w.profiles.profiles[OWNER] = dataclasses.replace(w.profiles.profiles[OWNER], import_done=False)
    assert await w.run(run) == {"failed": "profile_missing"}
    assert w.get(run).error_step is RunStep.PLAN_QUERIES and w.agents.calls == []


async def test_a_run_without_documents_finishes() -> None:
    w = World()
    run = await w.start(())
    result = await w.run(run)
    assert result["status"] == "done", result
    assert w.agents.count("InsightTopicTagger") == 0  # nothing to tag
    assert_user_view_clean(w.store)


# ---- several hostile drafts at once -----------------------------------------------------------------------------------
async def test_drafts_made_only_of_hostile_claims_are_never_shown_and_never_reach_the_critic() -> None:
    w = World()
    names = [
        "hallucinated-work-id",
        "stitched-quote",
        "email-in-claim",
        "claim-without-evidence",
        "altered-chunk-quote",
    ]
    script_drafts(
        w,
        *(
            draft(
                TITLES[i],
                [
                    CODE_CASES[n].claim,
                    claim("Second unsupported statement.", work_ref("W424242", "Study 424242 reports it")),
                ],
            )
            for i, n in enumerate(names)
        ),
    )
    run = await w.start()
    result = await w.run(run)
    assert result["status"] == "done"
    opps = w.opps(run)
    assert len(opps) == 5
    for o in opps:
        assert o.status is OppStatus.UNVERIFIED and o.status_reason == "no_verified_claims"
        assert len(o.claims) == 2 and all(c.status is ClaimStatus.UNVERIFIED for c in o.claims)
        assert o.visible_claims() == [] and o.verify_state == "done"
    assert w.agents.count("Critic") == 0
    assert not w.shown(run)
    assert_user_view_clean(w.store)


async def test_good_and_hostile_drafts_side_by_side_show_only_the_good_one() -> None:
    w = World()
    script_drafts(
        w,
        draft(TITLES[1], [CODE_CASES["stitched-quote"].claim, CODE_CASES["altered-quote"].claim]),
        draft(TITLES[2], good_claims()),
        draft(TITLES[3], [CODE_CASES["claim-without-evidence"].claim, CODE_CASES["count-mismatch"].claim]),
    )
    run = await w.start()
    await w.run(run)
    shown = w.shown(run)
    assert [o.title for o in shown] == [TITLES[2]]
    assert len(w.opps(run)) == 3, "nothing is deleted"
    assert_user_view_clean(w.store)


# ---- model strings are cleaned before they are stored ---------------------------------------------------------------
async def test_control_characters_are_stripped_and_oversize_claims_are_cut_before_storage() -> None:
    w = World()
    script_drafts(
        w,
        draft(
            "Cleaned\x00 title for\x07 the foams",
            [claim("The corpus\x00 work reports the key effect. " + "y" * 400, work_ref()), good_claims()[1]],
        ),
    )
    run = await w.start()
    await w.run(run)
    [opp] = w.opps(run)
    assert "\x00" not in opp.title and "\x07" not in opp.title
    assert all("\x00" not in c.text and len(c.text) <= 300 for c in opp.claims)


# ---- e-mail addresses outside claim text ---------------------------------------------------------------------------------
SRC_BUG_4 = src_bug(
    4,
    "design 3 says claims, titles and next steps with an e-mail are personal_data and never shown, but only claim "
    "text is checked (verification.check_claim); score_persist shows a draft whose title or next step holds an "
    "address. Run EMAIL over title, one_line and next_steps in verify and park the draft as unverified/personal_data",
)


@SRC_BUG_4
async def test_a_title_with_an_email_address_is_never_shown() -> None:
    w = World()
    script_drafts(w, draft("Collaborate with jane.doe@example.edu on foams", good_claims()))
    run = await w.start()
    await w.run(run)
    assert not w.shown(run)


@SRC_BUG_4
async def test_a_next_step_with_an_email_address_is_never_shown() -> None:
    w = World()
    d = draft(TITLES[0], good_claims()).model_copy(
        update={"next_steps": ["Write to jane.doe@example.edu this week"]}
    )
    script_drafts(w, d)
    run = await w.start()
    await w.run(run)
    assert not [o for o in w.shown(run) if any("@" in s for s in o.next_steps)]


# ---- canary: private text never leaves through errors, results or logs ----------------------------------------------
CANARY = "CANARY-5e1b9c-private-sentence"


def with_canary(w: World) -> None:
    """The canary sits in the passage text and in the insight summary, away from any quote the analyst makes."""
    owner, mat, ready = w.vault.docs[DOC_ID]
    passage = dataclasses.replace(mat.passages[0], text=f"{mat.passages[0].text} {CANARY}")
    insight = dataclasses.replace(mat.insights[0], text=f"Develop lignin foams {CANARY}")
    w.vault.docs[DOC_ID] = (owner, dataclasses.replace(mat, passages=(passage,), insights=(insight,)), ready)


def leaks(*things: object) -> list[str]:
    return [repr(t)[:80] for t in things if CANARY in repr(t)]


def stored_state(w: World) -> list[object]:
    return [w.store.runs, w.store.corpus, w.store.clusters, w.store.probes, w.store.opportunities]


async def test_a_successful_run_stores_none_of_the_private_text_beyond_its_quotes() -> None:
    w = World()
    with_canary(w)
    run = await w.start()
    with capture_logs() as logs:
        result = await w.run(run)
    assert w.get(run).status is RunStatus.DONE and w.shown(run)
    assert leaks(result, logs, *stored_state(w)) == []
    # the canary did reach the model inputs (so the test is not vacuous) and only those
    assert any(
        CANARY in d.model_dump_json() for a, d in w.agents.calls if a in ("QueryPlanner", "GapAnalyst")
    )


@pytest.mark.parametrize("last", [False, True])
async def test_an_error_that_quotes_the_private_text_does_not_reach_the_run_or_the_logs(last: bool) -> None:
    w = World()
    with_canary(w)

    def boom(data: Any) -> Any:
        raise RuntimeError(f"model failed on: {data.chunks[0].text}")

    w.agents.script("GapAnalyst", boom)
    run = await w.start()
    with capture_logs() as logs, pytest.raises(RuntimeError) as exc:
        await w.run(run, last=last)
    assert CANARY in str(exc.value)  # the exception itself is the worker's business (F-17)
    stored = w.get(run)
    assert leaks(stored, logs, *stored_state(w)) == []
    if last:
        assert stored.status is RunStatus.FAILED and stored.error_code == "internal_error"
        assert any(e["event"] == "discovery.run_failed" and e["reason"] == "internal_error" for e in logs)


@pytest.mark.parametrize("agent", ["QueryPlanner", "Critic"])
async def test_an_llm_error_carrying_private_text_leaves_only_a_code(agent: str) -> None:
    w = World()
    with_canary(w)
    raising(agent)(w, LLMUnavailable(f"upstream echoed: {CANARY}"))
    run = await w.start()
    with capture_logs() as logs:
        try:
            result: object = await w.run(run, last=True)
        except LLMUnavailable:
            result = None
    stored = w.get(run)
    if agent == "QueryPlanner":
        assert result is None and stored.error_code == "llm_unavailable"
    else:
        assert stored.status is RunStatus.DONE  # a Critic outage on the last attempt degrades instead
    assert leaks(result, stored, logs, *stored_state(w)) == []


async def test_model_output_with_the_canary_does_not_leak_through_a_later_failure() -> None:
    w = World()
    with_canary(w)
    script_drafts(
        w,
        draft(
            TITLES[0],
            [
                claim(f"Echoing the note: {CANARY}", chunk_ref("d1.c1", f"develop lignin {CANARY}")),
                good_claims()[0],
            ],
        ),
    )
    raising("Critic")(w, RuntimeError(f"critic crashed on {CANARY}"))
    run = await w.start()
    with capture_logs() as logs, pytest.raises(RuntimeError):
        await w.run(run, last=True)
    stored = w.get(run)
    assert stored.status is RunStatus.FAILED and stored.error_code == "internal_error"
    [opp] = w.opps(run)
    assert opp.claims[0].status is ClaimStatus.PENDING  # the draft is kept for analysis, unverified
    assert opp.visible_claims() == []
    assert leaks(stored, logs, w.store.runs, w.store.corpus, w.store.clusters, w.store.probes) == []


async def test_job_results_never_carry_text_even_when_a_step_fails_permanently() -> None:
    w = World()
    with_canary(w)
    w.literature.works = []
    run = await w.start()
    with capture_logs() as logs:
        result = await w.run(run)
    assert result == {"failed": "corpus_empty"}
    assert leaks(result, logs, w.get(run)) == []
    assert await w.run(run) == {"skipped": "failed"}
    assert call_names(w) == ["QueryPlanner", "search", "search"]
