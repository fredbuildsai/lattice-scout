"""Agent output models clean and bound everything a model returns."""

import pytest
from pydantic import ValidationError

from scout.discovery.domain.agent_io import (
    ClusterLabel,
    CriticVerdict,
    GapAnalysis,
    InsightTopicTags,
    QueryPlan,
)


def test_query_plan_is_cleaned_clamped_and_trimmed() -> None:
    plan = QueryPlan.model_validate(
        {"queries": [{"search": "lig\x00nin " + "x" * 200, "max_results": 9999, "purpose": "core"}] * 40}
    )
    assert (
        len(plan.queries) == 25 and "\x00" not in plan.queries[0].search and len(plan.queries[0].search) <= 80
    )
    assert plan.queries[0].max_results == 200
    assert (
        QueryPlan.model_validate({"queries": [{"search": "a", "max_results": 0}]}).queries[0].max_results == 1
    )
    with pytest.raises(ValidationError):
        QueryPlan.model_validate({"queries": []})
    with pytest.raises(
        ValidationError
    ):  # an unknown purpose is a model mistake, not something to guess around
        QueryPlan.model_validate({"queries": [{"search": "a", "purpose": "random"}]})


def test_cluster_label_bounds() -> None:
    label = ClusterLabel.model_validate(
        {
            "cluster_id": "k1",
            "label": "L" * 100,
            "search_terms": ["a", "b", "c", "d", "e", "f"],
            "extra": "ignored",
        }
    )
    assert len(label.label) == 60 and label.search_terms == ["a", "b", "c", "d", "e"]


def test_gap_analysis_bounds_and_defaults() -> None:
    draft = {
        "title": "T" * 300, "claims": [{"text": "c" * 500, "evidence_refs": [{"type": "work", "id": "W1", "quote": "q" * 900}] * 9}] * 12,
        "next_steps": ["s"] * 9,
    }  # fmt: skip
    out = GapAnalysis.model_validate({"opportunities": [draft] * 8})
    opp = out.opportunities[0]
    assert len(out.opportunities) == 5 and len(opp.title) == 120 and len(opp.claims) == 8
    assert len(opp.claims[0].text) == 300 and len(opp.claims[0].evidence_refs) == 4
    assert len(opp.claims[0].evidence_refs[0].quote or "") == 600 and len(opp.next_steps) == 3
    assert opp.claims[0].section == "rationale" and opp.kind == "white_spot"
    with pytest.raises(ValidationError):  # a draft without claims is unusable
        GapAnalysis.model_validate({"opportunities": [{"title": "x", "claims": []}]})
    with pytest.raises(ValidationError):
        GapAnalysis.model_validate(
            {
                "opportunities": [
                    {"title": "x", "claims": [{"text": "c", "evidence_refs": [{"type": "url", "id": "u"}]}]}
                ]
            }
        )


def test_tags_and_verdict_defaults() -> None:
    tags = InsightTopicTags.model_validate(
        {"tags": [{"insight_ref": "d1.aims[0]", "cluster_ids": ["k1", "k2", "k3", "k4"]}]}
    )
    assert tags.tags[0].cluster_ids == ["k1", "k2", "k3"] and tags.tags[0].strength == "partial"
    verdict = CriticVerdict.model_validate(
        {"claims": [{"index": 0, "status": "verified", "reason": "r\x00"}], "reasons": list("abcde")}
    )
    assert verdict.claims[0].reason == "r" and verdict.keep is False and len(verdict.reasons) == 3
