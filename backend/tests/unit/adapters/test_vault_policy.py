"""Vault-tagged calls reach only sanctioned deployments (ADR 015); public calls are unaffected."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

pytest.importorskip("llmrouter_free")

from llmrouter_free import LLMRouter
from tests.fakes import InMemoryUsageMeter

from scout.adapters.llm.llm_router import LlmRouterAdapter, MeteringCompletion
from scout.adapters.llm.vault_policy import VaultPolicy, build_router_config
from scout.ports.llm import AgentSpec, CallContext, DataClass, ModelTier, VaultDataRefused
from scout.ports.usage import PriceTable
from scout.shared import FixedClock, SequentialIdGenerator

CONFIG: dict[str, Any] = {
    "deployments": [
        {"name": "free", "model": "openrouter/x:free", "api_key_env": "TEST_KEY"},
        {"name": "eu", "model": "mistral/small", "api_key_env": "TEST_KEY", "sanctioned": True},
        {"name": "marked-only", "model": "groq/y", "api_key_env": "TEST_KEY", "sanctioned": True},
    ],
    "routes": {"fast": ["free", "marked-only", "eu"], "reasoning": ["free"]},
    "cooldown": {"rate_limit_seconds": 0, "error_seconds": 0},
}


class Out(BaseModel):
    answer: str


def _spec(data_class: DataClass) -> AgentSpec[Out]:
    return AgentSpec(
        name="T", prompt_version="1", tier=ModelTier.FAST, instructions="SECRET-PROMPT", output_type=Out,
        data_class=data_class,
    )  # fmt: skip


def _adapter(providers: set[str], allow_any: bool = False) -> tuple[LlmRouterAdapter, list[str]]:
    os.environ["TEST_KEY"] = "x"
    seen: list[str] = []

    def completion(**kwargs: Any) -> Any:
        seen.append(kwargs["model"])
        msg = SimpleNamespace(content='{"answer": "ok"}')
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)

    cfg, policy = build_router_config(CONFIG, providers, allow_any=allow_any)
    router = LLMRouter(cfg, completion_fn=MeteringCompletion(completion), sleep=lambda s: None)
    adapter = LlmRouterAdapter(
        router, PriceTable(version="t", models={}), InMemoryUsageMeter(),
        FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), SequentialIdGenerator(),
        {ModelTier.FAST: "fast", ModelTier.REASONING: "reasoning"}, ThreadPoolExecutor(2), policy,
    )  # fmt: skip
    return adapter, seen


async def test_vault_call_uses_only_marked_deployments_of_allowed_providers() -> None:
    adapter, seen = _adapter({"mistral"})
    await adapter.run(_spec(DataClass.VAULT), {"q": "private"}, CallContext(feature="f"))
    assert seen == ["mistral/small"]  # not the free openrouter model, not groq (not on the allowlist)


async def test_vault_call_refused_with_empty_allowlist_and_nothing_sent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, seen = _adapter(set())
    with caplog.at_level(logging.ERROR), pytest.raises(VaultDataRefused):
        await adapter.run(_spec(DataClass.VAULT), {"q": "private-text"}, CallContext(feature="f"))
    assert seen == []
    assert "private-text" not in caplog.text and "SECRET-PROMPT" not in caplog.text
    assert "vault_data_refused" in caplog.text


async def test_vault_call_refused_when_route_has_no_sanctioned_deployment() -> None:
    adapter, seen = _adapter({"mistral"})
    reasoning = AgentSpec(
        name="R", prompt_version="1", tier=ModelTier.REASONING, instructions="i", output_type=Out,
        data_class=DataClass.VAULT,
    )  # fmt: skip
    with pytest.raises(VaultDataRefused):
        await adapter.run(reasoning, {}, CallContext(feature="f"))
    assert seen == []


async def test_public_call_may_use_any_deployment() -> None:
    adapter, seen = _adapter(set())
    await adapter.run(_spec(DataClass.PUBLIC), {"q": "papers"}, CallContext(feature="f"))
    assert seen == ["openrouter/x:free"]


async def test_allow_any_waiver_lifts_the_rule() -> None:
    adapter, seen = _adapter(set(), allow_any=True)
    await adapter.run(_spec(DataClass.VAULT), {}, CallContext(feature="f"))
    assert seen == ["openrouter/x:free"]


def test_unmarked_provider_on_allowlist_is_not_sanctioned() -> None:
    _, policy = build_router_config(CONFIG, {"openrouter"})
    assert policy.sanctioned_models == frozenset() and policy.vault_routes == {}


def test_check_model_blocks_unsanctioned_model() -> None:
    policy = VaultPolicy(sanctioned_models=frozenset({"mistral/small"}))
    policy.check_model("mistral/small")
    with pytest.raises(VaultDataRefused):
        policy.check_model("openrouter/x:free")


def test_agent_spec_defaults_to_vault_and_prompts_declare_a_class() -> None:
    from pathlib import Path

    from scout.adapters.llm.prompts import load_agent_spec

    assert (
        AgentSpec(
            name="n", prompt_version="1", tier=ModelTier.FAST, instructions="", output_type=Out
        ).data_class
        is DataClass.VAULT
    )
    prompts = Path(__file__).resolve().parents[3] / "prompts"
    for f in prompts.glob("[A-Z]*.md"):
        assert "\ndata_class: " in f.read_text(), f"{f.name} must declare data_class"
    _ = load_agent_spec  # loader reads the key (covered by the factory tests)
