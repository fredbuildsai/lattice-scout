"""The live eval harness against a scripted fake model: report math, rule checks, go / no-go, safety gates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from live_eval import backends
from live_eval.cases import ALL_AGENTS, PUBLISHED_AGENTS, EvalCase, build_cases
from live_eval.cli import live_main
from live_eval.report import (
    GO,
    GO_PUBLISHED,
    INCOMPLETE,
    NO_GO,
    CallRecord,
    build_report,
    load_thresholds,
    percentile,
)
from live_eval.runner import Attempts, Backend, _guard, make_factory, run_backend
from live_eval.scripted import ScriptedLLM
from scout.ports.llm import ConfidentialDataRefused

ROOT = Path(__file__).resolve().parents[4]
THRESHOLDS = load_thresholds(ROOT / "eval" / "thresholds.yaml")
CASES = build_cases()


async def run(
    behavior: str,
    *,
    allow: bool = True,
    max_calls: int = 500,
    runs: int = 1,
    cases: list[EvalCase] | None = None,
) -> tuple[list[CallRecord], list[str], dict[str, Any], Backend, ScriptedLLM]:
    cases = cases if cases is not None else CASES
    at = Attempts()
    llm = ScriptedLLM(cases, at, behavior)
    backend = Backend(behavior, llm, at, allow)
    records, notes = await run_backend(
        backend, make_factory(llm, ROOT / "prompts"), cases, runs=runs, max_calls=max_calls
    )
    report = build_report(records, THRESHOLDS, {"at": "test"})
    return records, notes, report["deployments"][behavior], backend, llm


def test_case_inventory() -> None:
    by = {a: [c for c in CASES if c.agent == a] for a in ALL_AGENTS}
    assert all(by[a] for a in ALL_AGENTS)
    assert (
        sum(c.label == "bad" for c in by["Critic"]) >= 6 and sum(c.label == "good" for c in by["Critic"]) >= 6
    )
    assert len(by["MatchExplainer"]) >= 10
    assert all(c.synthetic for c in CASES if c.agent not in PUBLISHED_AGENTS)
    assert len({c.id for c in CASES}) == len(CASES)


def test_percentile_nearest_rank() -> None:
    assert percentile([], 50) is None
    assert percentile(list(map(float, range(1, 11))), 50) == 5.0
    assert percentile(list(map(float, range(1, 11))), 95) == 10.0


async def test_perfect_model_is_go() -> None:
    records, notes, dep, backend, _ = await run("perfect")
    assert dep["verdict"] == GO and dep["reasons"] == [] and notes == []
    assert len(records) == len(CASES) == backend.used_calls
    for agent, a in dep["agents"].items():
        m = a["metrics"]
        assert a["evaluation"]["status"] == "pass", agent
        assert m["schema_valid_rate"] == m["rule_pass_rate"] == 1.0
        assert m["invented_id_count"] == 0 and m["failure_codes"] == {}
    critic = dep["agents"]["Critic"]["metrics"]
    assert critic["bad_catch_rate"] == 1.0 and critic["good_keep_rate"] == 1.0
    assert dep["agents"]["MatchExplainer"]["metrics"]["anonymity_leak_rate"] == 0.0
    assert dep["agents"]["GapAnalyst"]["metrics"]["quote_verbatim_rate"] == 1.0


async def test_invalid_json_30_percent() -> None:
    records, _, dep, _, llm = await run("invalid_json")
    invalid = sum(1 for r in records if r.status == "invalid")
    assert (
        invalid == sum(1 for n in range(1, llm.calls + 1) if n % 10 < 3)
        and 0.25 < invalid / len(records) < 0.4
    )
    for agent, a in dep["agents"].items():
        m = a["metrics"]
        assert m["invalid"] == sum(1 for r in records if r.agent == agent and r.status == "invalid")
        if m["calls"]:
            assert m["schema_valid_rate"] == pytest.approx(m["ok"] / (m["ok"] + m["invalid"]), abs=1e-4)
    assert dep["verdict"] == NO_GO and "min_schema_valid_rate" in " ".join(dep["reasons"])


async def test_always_approving_critic_is_caught() -> None:
    _, _, dep, _, _ = await run("approve_all")
    critic = dep["agents"]["Critic"]
    assert critic["metrics"]["bad_catch_rate"] == 0.0 and critic["metrics"]["good_keep_rate"] == 1.0
    assert critic["metrics"]["failure_codes"] == {"planted_claim_approved": 8}
    assert critic["evaluation"]["status"] == "fail"
    assert dep["verdict"] == NO_GO and dep["reasons"][0].startswith("Critic: failed")
    assert (
        dep["agents"]["MatchExplainer"]["evaluation"]["status"] == "pass"
    )  # the other agents are unaffected


async def test_leaky_explainer_and_joint_ideas_fail_the_anonymity_guard() -> None:
    _, _, dep, _, _ = await run("leaky")
    assert dep["agents"]["MatchExplainer"]["metrics"]["anonymity_leak_rate"] == 1.0
    assert dep["agents"]["JointIdeas"]["metrics"]["anonymity_leak_rate"] == 1.0
    assert dep["verdict"] == NO_GO


async def test_schema_rejection_falls_back_to_json_mode_once() -> None:
    records, _, dep, backend, _ = await run("strict_reject")
    assert (
        backend.json_mode and sum(r.schema_rejected for r in records) == 1
    )  # only the first case hit the rejection
    first = records[0]
    assert first.fallback_used and first.status == "ok" and first.provider_calls == 2
    assert all(r.schema_mode == "json_mode" for r in records)
    m = dep["agents"][first.agent]["metrics"]
    assert m["schema_rejections"] >= 1 and m["fallback_ok_rate"] == 1.0
    assert dep["verdict"] == GO


async def test_rate_limits_are_counted_as_events_and_retries() -> None:
    records, _, dep, backend, llm = await run("rate_limited")
    expected = sum(1 for n in range(1, llm.calls + 1) if n % 4 == 0)
    assert (
        sum(r.rate_limited for r in records)
        == sum(a["metrics"]["rate_limit_events"] for a in dep["agents"].values())
        == expected
    )
    assert sum(r.retries for r in records) == expected and backend.used_calls == len(records) + expected
    assert dep["verdict"] == GO


async def test_confidential_class_agents_are_not_sent_unless_listed() -> None:
    records, notes, dep, _, llm = await run("perfect", allow=False)
    published = [c for c in CASES if c.agent in PUBLISHED_AGENTS]
    assert llm.calls == len(published) == len(records)
    assert {r.agent for r in records} == set(PUBLISHED_AGENTS)
    assert any("skipped" in n for n in notes)
    assert dep["verdict"] == GO_PUBLISHED and "Critic: not_run" in dep["reasons"]


async def test_call_cap_is_a_hard_stop() -> None:
    records, notes, dep, backend, llm = await run("perfect", max_calls=10)
    assert backend.used_calls == llm.calls == len(records) == 10
    assert any("call cap" in n for n in notes)
    assert dep["verdict"] in (INCOMPLETE, GO_PUBLISHED, NO_GO)
    assert (
        len({r.agent for r in records}) >= 9 - 1
    )  # round-robin: a small cap still samples (almost) every agent


async def test_runs_multiply_calls() -> None:
    records, _, _, backend, _ = await run(
        "perfect", runs=2, cases=[c for c in CASES if c.agent == "ClusterLabeler"]
    )
    assert len(records) == 16 == backend.used_calls and {r.run for r in records} == {0, 1}


def test_guard_refuses_a_non_synthetic_confidential_case() -> None:
    real = EvalCase("x", "Critic", "confidential", {"draft": "vault text"}, {}, synthetic=False)
    with pytest.raises(ConfidentialDataRefused):
        _guard(real)


async def test_report_holds_no_model_or_input_text() -> None:
    records, _, _, _, _ = await run("perfect", cases=[c for c in CASES if c.agent == "Critic"])
    dumped = json.dumps([r.to_json() for r in records])
    assert "[SAMPLE]" not in dumped and "kraft lignin" not in dumped


async def test_cli_dry_run_writes_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "live-test.json"
    assert await live_main(["--live", "--dry-run", "--out", str(out)]) == 0
    assert (tmp_path / "live-test.md").read_text().startswith("# Live eval report")
    data = json.loads(out.read_text())
    assert data["deployments"]["scripted-perfect"]["verdict"] == GO and data["mode"] == "dry-run"
    assert await live_main(["--live", "--dry-run", "approve_all", "--out", str(out)]) == 1
    assert await live_main(["--live"]) == 2
    assert await live_main(["--live", "--dry-run", "--max-calls", "100000"]) == 2
    assert await live_main(["--live", "--dry-run", "--agents", "Nope"]) == 2
    capsys.readouterr()


def test_router_config_selection_and_free_only() -> None:
    config = backends.load_config(ROOT / "config" / "llm_router.yaml")
    assert backends.select_deployments(config, ["groq-gpt-oss"], None) == ["groq-gpt-oss"]
    assert "openrouter-nemotron-super" in backends.select_deployments(config, [], "reasoning")
    with pytest.raises(ValueError):
        backends.select_deployments(config, ["nope"], None)
    with pytest.raises(ValueError):
        backends.select_deployments(config, [], None)
    deps = {d["name"]: d for d in config["deployments"]}
    assert all(backends.not_free_reason(d) is None for d in deps.values())
    assert backends.not_free_reason({"name": "x", "model": "openrouter/some/model"}) is not None
    backend, _, why = backends.build_backend(
        config, "groq-gpt-oss", allow_synthetic_confidential=False, env={}
    )
    assert backend is None and why == "GROQ_API_KEY is not set"


def test_thresholds_name_known_agents() -> None:
    names = {*THRESHOLDS["required_published"], *THRESHOLDS["required_confidential"], *THRESHOLDS["agents"]}
    assert names <= set(ALL_AGENTS)
