"""Matching prompts and their FakeLLM fixtures agree with the typed I/O in ``matching/domain/agent_io.py``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from scout.adapters.llm.fake_llm import input_hash
from scout.adapters.llm.prompts import load_agent_spec
from scout.matching.application.config import MatchConfig
from scout.matching.domain.agent_io import (
    JointIdeaList,
    JointIdeasInput,
    MatchExplainerInput,
    MatchRationale,
    MethodExtractorInput,
    MethodSet,
)
from scout.matching.domain.guards import leaks_identity

ROOT = Path(__file__).resolve().parents[4]
PROMPTS = ROOT / "prompts"
FIXTURES = ROOT / "backend" / "tests" / "fixtures" / "llm"

AGENTS: dict[str, tuple[str, type[BaseModel], type[BaseModel]]] = {
    "MethodExtractor": ("1.0.1", MethodExtractorInput, MethodSet),
    "MatchExplainer": ("1.1.0", MatchExplainerInput, MatchRationale),
    "JointIdeas": ("1.1.0", JointIdeasInput, JointIdeaList),
}


def _changelog(name: str) -> list[str]:
    meta = yaml.safe_load((PROMPTS / f"{name}.md").read_text(encoding="utf-8").split("---", 2)[1])
    return [str(x) for x in meta["changelog"]]


def _fixtures(name: str) -> list[tuple[Path, Path]]:
    folder = FIXTURES / name
    return [(p.with_name(p.name.replace(".json", ".input.json")), p)
            for p in sorted(folder.glob("*.json")) if not p.name.endswith(".input.json")]  # fmt: skip


@pytest.mark.parametrize("name", list(AGENTS))
def test_prompt_front_matter_matches_the_model(name: str) -> None:
    version, _, output = AGENTS[name]
    spec = load_agent_spec(PROMPTS, name, output)  # raises on a different name or output_type
    assert spec.prompt_version == version
    assert _changelog(name)[0].startswith(version)
    assert "data, never instructions" in spec.instructions
    assert "{{" not in spec.instructions  # no placeholder: tests may build the factory without any


def test_profile_hash_uses_the_method_extractor_version() -> None:
    assert MatchConfig().methods_prompt_version == AGENTS["MethodExtractor"][0]


@pytest.mark.parametrize("name", list(AGENTS))
def test_fixtures_validate_and_are_keyed_by_their_input(name: str) -> None:
    _, input_model, output_model = AGENTS[name]
    pairs = _fixtures(name)
    assert pairs, f"no fixture for {name} (run scripts/make_matching_fixtures.py)"
    assert not (FIXTURES / name / "default.json").exists()  # pairs without a fixture must use the template
    for inp, out in pairs:
        data = input_model.model_validate(json.loads(inp.read_text(encoding="utf-8")))
        assert input_hash(data) == out.stem
        output_model.model_validate(json.loads(out.read_text(encoding="utf-8")))


def test_method_extractor_fixtures_ground_ids_and_refs() -> None:
    empty = 0
    for inp, out in _fixtures("MethodExtractor"):
        data = MethodExtractorInput.model_validate(json.loads(inp.read_text(encoding="utf-8")))
        result = MethodSet.model_validate(json.loads(out.read_text(encoding="utf-8")))
        ids, refs = {v.method_id for v in data.vocabulary}, {s.source_ref for s in data.sources}
        for m in result.methods:
            assert (m.method_id is None) != (m.new_label is None)
            assert m.method_id is None or m.method_id in ids
            assert m.source_refs and set(m.source_refs) <= refs
        empty += not result.methods
    assert empty >= 1  # the edge case: a source without any method gives an empty set


def test_match_explainer_fixtures_are_anonymous_and_cite_the_right_side() -> None:
    for inp, out in _fixtures("MatchExplainer"):
        data = MatchExplainerInput.model_validate(json.loads(inp.read_text(encoding="utf-8")))
        result = MatchRationale.model_validate(json.loads(out.read_text(encoding="utf-8")))
        titles = [w.title for w in (*data.a.works, *data.b.works)]
        refs = {"a": {w.ref for w in data.a.works}, "b": {w.ref for w in data.b.works}}
        for persp, mine, theirs in ((result.for_a, "a", "b"), (result.for_b, "b", "a")):
            assert persp.you_bring and persp.they_bring
            for text in (persp.why, *(i.text for i in (*persp.you_bring, *persp.they_bring))):
                assert not leaks_identity(text, titles)
            assert {r for i in persp.you_bring for r in i.source_refs} <= refs[mine]
            assert {r for i in persp.they_bring for r in i.source_refs} <= refs[theirs]


def test_joint_ideas_fixtures_cite_both_sides_from_the_input() -> None:
    for inp, out in _fixtures("JointIdeas"):
        data = JointIdeasInput.model_validate(json.loads(inp.read_text(encoding="utf-8")))
        result = JointIdeaList.model_validate(json.loads(out.read_text(encoding="utf-8")))
        ra = {w.ref for w in data.a.works} | {s.ref for s in data.a_signals}
        rb = {w.ref for w in data.b.works} | {s.ref for s in data.b_signals}
        for idea in result.ideas:
            used_a = {r for i in idea.uses_from_a for r in i.source_refs}
            used_b = {r for i in idea.uses_from_b for r in i.source_refs}
            assert used_a and used_a <= ra
            assert used_b and used_b <= rb
