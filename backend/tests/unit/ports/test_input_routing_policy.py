"""The confidential/published routing policy (ADR 016): pure, no I/O."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from scout.discovery.domain.agent_io import (
    ChunkForAnalyst,
    CriticDraft,
    CriticInput,
    EvidenceText,
    GapContext,
    InsightForAnalyst,
    InsightTopicTaggerInput,
    PlanField,
    ProfileSummary,
    QueryPlannerInput,
    TagCluster,
    TagInsight,
)
from scout.matching.domain.agent_io import MethodExtractorInput, MethodSource
from scout.shared.confidential import (
    AgentInput,
    DataClass,
    confidential_field,
    contains_confidential,
    input_for_route,
    published_field,
    withhold_confidential,
)

SECRET = "SECRET-UNPUBLISHED-TEXT"


def _gap() -> GapContext:
    return GapContext(
        profile_summary=ProfileSummary(topics=["t"], recent_works=[]),
        insights=[InsightForAnalyst(insight_ref="d1.aims[0]", document="d1", section="aims", text=SECRET)],
        coverage=[], novelty=[], papers=[],
        chunks=[ChunkForAnalyst(chunk_id="d1.c1", document="d1", page=1, text=SECRET)],
    )  # fmt: skip


def test_confidential_parts_are_dropped_on_an_unauthorized_route_and_the_rest_kept() -> None:
    sent = input_for_route(DataClass.CONFIDENTIAL, _gap(), authorized=False)
    assert sent is not None
    model, klass = sent
    assert klass is DataClass.PUBLISHED and SECRET not in model.model_dump_json()
    assert isinstance(model, GapContext) and model.profile_summary.topics == ["t"] and model.insights == []


def test_everything_is_kept_on_an_authorized_route_and_the_class_says_confidential() -> None:
    model, klass = input_for_route(DataClass.CONFIDENTIAL, _gap(), authorized=True)  # type: ignore[misc]
    assert klass is DataClass.CONFIDENTIAL and SECRET in model.model_dump_json()


def test_an_input_without_confidential_content_is_published_even_on_an_authorized_route() -> None:
    empty = _gap().model_copy(update={"insights": [], "chunks": []})
    assert input_for_route(DataClass.CONFIDENTIAL, empty, authorized=True)[1] is DataClass.PUBLISHED  # type: ignore[index]
    assert not contains_confidential(empty)


def test_a_wholly_confidential_input_has_no_published_form() -> None:
    data = InsightTopicTaggerInput(
        clusters=[TagCluster(cluster_id="k1", label="L")], insights=[TagInsight(insight_ref="r", text=SECRET)]
    )
    assert input_for_route(DataClass.CONFIDENTIAL, data, authorized=False) is None
    assert input_for_route(DataClass.CONFIDENTIAL, data, authorized=True) is not None


def test_list_items_classify_themselves() -> None:
    data = MethodExtractorInput(
        vocabulary=[],
        sources=[MethodSource(source_ref="work:W1", text="abstract"), MethodSource(source_ref="insight:d.m[0]", text=SECRET)],
    )  # fmt: skip
    sent = input_for_route(DataClass.CONFIDENTIAL, data, authorized=False)
    assert sent is not None and isinstance(sent[0], MethodExtractorInput)
    assert [s.source_ref for s in sent[0].sources] == ["work:W1"]
    evidence = CriticInput(
        draft=CriticDraft(title="t", kind="k", claims=[]),
        evidence_texts=[
            EvidenceText(ref_id="W1", type="work", text="abstract"),
            EvidenceText(ref_id="n1", type="count", text="3 works"),
            EvidenceText(ref_id="d1.c1", type="chunk", text=SECRET),
            EvidenceText(ref_id="d1.aims[0]", type="insight", text=SECRET),
        ],
    )
    kept = withhold_confidential(evidence)
    assert [e.ref_id for e in kept.evidence_texts] == ["W1", "n1"]


def test_unknown_input_never_goes_out_when_in_doubt() -> None:
    class Loose(BaseModel):  # not an AgentInput: nothing says what it contains
        text: str = SECRET

    assert contains_confidential(Loose())
    assert input_for_route(DataClass.CONFIDENTIAL, Loose(), authorized=False) is None
    assert input_for_route(DataClass.CONFIDENTIAL, Loose(), authorized=True) is not None


def test_a_published_agent_whose_input_carries_confidential_parts_is_still_protected() -> None:
    model, klass = input_for_route(DataClass.PUBLISHED, _gap(), authorized=False)  # type: ignore[misc]
    assert klass is DataClass.PUBLISHED and SECRET not in model.model_dump_json()
    # a genuinely published input passes untouched
    plain = QueryPlannerInput(topics=[], field=PlanField(name="f"), year_from=2020)
    assert input_for_route(DataClass.PUBLISHED, plain, authorized=False) == (plain, DataClass.PUBLISHED)


def test_unclassified_fields_are_rejected_when_the_input_class_is_defined() -> None:
    with pytest.raises(TypeError, match="classify"):

        class Bad(AgentInput):
            text: str = ""


def test_a_confidential_field_without_default_cannot_be_emptied() -> None:
    class Needs(AgentInput):
        p: str = published_field(default="x")
        c: str = confidential_field()

    with pytest.raises(ValueError, match="no default"):
        withhold_confidential(Needs(p="a", c="b"))
    assert input_for_route(DataClass.CONFIDENTIAL, Needs(p="a", c="b"), authorized=False) is None


def test_withholding_does_not_modify_the_original() -> None:
    g = _gap()
    withhold_confidential(g)
    assert g.insights and g.chunks


def test_default_factory_fields_are_reset_to_fresh_defaults() -> None:
    class M(AgentInput):
        notes: list[str] = confidential_field(default_factory=list)
        keep: int = published_field(default=1)

    m = M(notes=[SECRET])
    out = withhold_confidential(m)
    assert out.notes == [] and out.notes is not m.notes and out.keep == 1
