"""Confidential calls reach only authorized deployments (ADR 016); published calls are unaffected."""

import logging
import os
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

pytest.importorskip("llmrouter_free")

from llmrouter_free import LLMRouter
from tests.fakes import InMemoryUsageMeter

from scout.adapters.llm.confidential_policy import ConfidentialPolicy, build_router_config
from scout.adapters.llm.llm_router import LlmRouterAdapter, MeteringCompletion
from scout.ports.llm import (
    AgentSpec,
    CallContext,
    ConfidentialDataRefused,
    DataClass,
    LLMUnavailable,
    ModelTier,
)
from scout.ports.usage import PriceTable
from scout.shared import FixedClock, SequentialIdGenerator

CONFIG: dict[str, Any] = {
    "deployments": [
        {"name": "free", "model": "openrouter/x:free", "api_key_env": "TEST_KEY"},
        {"name": "eu", "model": "mistral/small", "api_key_env": "TEST_KEY", "authorized": True},
        {"name": "marked-only", "model": "groq/y", "api_key_env": "TEST_KEY", "authorized": True},
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


def _adapter(
    providers: set[str], allow_any: bool = False, fail: Mapping[str, Exception] | None = None
) -> tuple[LlmRouterAdapter, list[str]]:
    """``fail`` maps a model name to the exception its deployment raises (the call is still recorded in ``seen``)."""
    os.environ["TEST_KEY"] = "x"
    seen: list[str] = []

    def completion(**kwargs: Any) -> Any:
        seen.append(kwargs["model"])
        if kwargs["model"] in (fail or {}):
            raise (fail or {})[kwargs["model"]]
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


async def test_confidential_call_uses_only_marked_deployments_of_allowed_providers() -> None:
    adapter, seen = _adapter({"mistral"})
    await adapter.run(_spec(DataClass.CONFIDENTIAL), {"q": "private"}, CallContext(feature="f"))
    assert seen == ["mistral/small"]  # not the free openrouter model, not groq (not on the allowlist)


async def test_confidential_call_refused_with_empty_allowlist_and_nothing_sent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, seen = _adapter(set())
    with caplog.at_level(logging.ERROR), pytest.raises(ConfidentialDataRefused):
        await adapter.run(_spec(DataClass.CONFIDENTIAL), {"q": "private-text"}, CallContext(feature="f"))
    assert seen == []
    assert "private-text" not in caplog.text and "SECRET-PROMPT" not in caplog.text
    assert "confidential_refused" in caplog.text


async def test_confidential_call_refused_when_route_has_no_authorized_deployment() -> None:
    adapter, seen = _adapter({"mistral"})
    reasoning = AgentSpec(
        name="R", prompt_version="1", tier=ModelTier.REASONING, instructions="i", output_type=Out,
        data_class=DataClass.CONFIDENTIAL,
    )  # fmt: skip
    with pytest.raises(ConfidentialDataRefused):
        await adapter.run(reasoning, {}, CallContext(feature="f"))
    assert seen == []


async def test_published_call_may_use_any_deployment() -> None:
    adapter, seen = _adapter(set())
    await adapter.run(_spec(DataClass.PUBLISHED), {"q": "papers"}, CallContext(feature="f"))
    assert seen == ["openrouter/x:free"]


async def test_allow_any_waiver_lifts_the_rule() -> None:
    adapter, seen = _adapter(set(), allow_any=True)
    await adapter.run(_spec(DataClass.CONFIDENTIAL), {}, CallContext(feature="f"))
    assert seen == ["openrouter/x:free"]


def test_unmarked_provider_on_allowlist_is_not_authorized() -> None:
    _, policy = build_router_config(CONFIG, {"openrouter"})
    assert policy.authorized_models == frozenset() and policy.confidential_routes == {}


def test_check_model_blocks_unauthorized_model() -> None:
    policy = ConfidentialPolicy(authorized_models=frozenset({"mistral/small"}))
    policy.check_model("mistral/small")
    with pytest.raises(ConfidentialDataRefused):
        policy.check_model("openrouter/x:free")


def test_agent_spec_defaults_to_vault_and_prompts_declare_a_class() -> None:
    from pathlib import Path

    from scout.adapters.llm.prompts import load_agent_spec

    assert (
        AgentSpec(
            name="n", prompt_version="1", tier=ModelTier.FAST, instructions="", output_type=Out
        ).data_class
        is DataClass.CONFIDENTIAL
    )
    prompts = Path(__file__).resolve().parents[3] / "prompts"
    for f in prompts.glob("[A-Z]*.md"):
        assert "\ndata_class: " in f.read_text(), f"{f.name} must declare data_class"
    _ = load_agent_spec  # loader reads the key (covered by the factory tests)


async def test_legacy_sanctioned_key_is_still_read() -> None:
    deployments = [
        {k: v for k, v in d.items() if k != "authorized"}
        | ({"sanctioned": True} if d.get("authorized") else {})
        for d in CONFIG["deployments"]
    ]
    cfg = {**CONFIG, "deployments": deployments}
    _, policy = build_router_config(cfg, {"mistral"})
    assert policy.authorized_models == frozenset({"mistral/small"})


def test_adapter_reports_whether_each_tier_is_authorized() -> None:
    adapter, _ = _adapter({"mistral"})
    assert adapter.confidential_allowed(ModelTier.FAST) is True  # route fast has an authorized member
    assert adapter.confidential_allowed(ModelTier.REASONING) is False  # route reasoning has none
    assert adapter.confidential_allowed() is False  # "in doubt" (any tier unauthorized) means no
    assert _adapter(set())[0].confidential_allowed() is False
    assert _adapter(set(), allow_any=True)[0].confidential_allowed() is True


class _RateLimited(Exception):
    status_code = 429


async def test_confidential_call_never_falls_over_to_an_unauthorized_deployment() -> None:
    """The pinned route holds authorized members only: when they fail, the call fails; the free model is never tried."""
    both = {"groq/y": _RateLimited("slow down"), "mistral/small": _RateLimited("slow down")}
    adapter, seen = _adapter({"mistral", "groq"}, fail=both)
    with pytest.raises(LLMUnavailable):
        await adapter.run(_spec(DataClass.CONFIDENTIAL), {"q": "x"}, CallContext(feature="f"))
    assert seen and set(seen) <= {"groq/y", "mistral/small"} and "openrouter/x:free" not in seen


async def test_provider_error_text_never_reaches_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    """A provider 400 can echo generated text (Groq ``failed_generation``): the log keeps the outcome, not the text."""
    from scout.api.logging import install_exception_scrubber

    install_exception_scrubber()
    secret = "SECRET-ECHOED-GENERATION"
    adapter, _ = _adapter({"mistral"}, fail={"mistral/small": RuntimeError(f"json_validate_failed {secret}")})
    with caplog.at_level(logging.DEBUG), pytest.raises(LLMUnavailable):
        await adapter.run(_spec(DataClass.CONFIDENTIAL), {"q": "x"}, CallContext(feature="f"))
    assert caplog.records, "the router should still log the failed attempt"
    assert secret not in caplog.text and all(secret not in r.getMessage() for r in caplog.records)
