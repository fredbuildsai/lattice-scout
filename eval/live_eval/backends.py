"""Real backends for the live eval: one ``Backend`` per llm-router deployment, free models only.

Needs the optional ``llmrouter_free`` package and the provider key in the environment. Keys are read by the router
from ``OPENROUTER_API_KEY`` / ``GROQ_API_KEY`` / ``MISTRAL_API_KEY``; they are never printed or stored here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml

from live_eval.runner import Attempts, Backend
from scout.adapters.llm.confidential_policy import build_router_config
from scout.adapters.llm.llm_router import LlmRouterAdapter, MeteringCompletion
from scout.adapters.observability.prices import load_price_table
from scout.ports.llm import ModelTier
from scout.shared import SystemClock, UuidGenerator

MAX_CALLS_CEILING = 500  # per deployment: a typo cannot burn a free quota


def load_config(path: Path) -> dict[str, Any]:
    """The llm-router YAML (deployments and routes)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def select_deployments(config: dict[str, Any], deployments: list[str], route: str | None) -> list[str]:
    """Names from ``--deployment`` (explicit) or the members of ``--route``; raises ``ValueError`` when unknown."""
    known = {d["name"] for d in config.get("deployments", [])}
    names = list(deployments)
    if route:
        if route not in config.get("routes", {}):
            raise ValueError(f"unknown route {route!r}; routes: {sorted(config.get('routes', {}))}")
        names += [n for n in config["routes"][route] if n not in names]
    unknown = [n for n in names if n not in known]
    if unknown:
        raise ValueError(f"unknown deployment(s) {unknown}; known: {sorted(known)}")
    if not names:
        raise ValueError(f"pass --deployment NAME[,NAME] or --route NAME; deployments: {sorted(known)}")
    return names


def not_free_reason(dep: dict[str, Any]) -> str | None:
    """Why a deployment may cost money (None = looks free). OpenRouter models must end in ``:free``."""
    model = str(dep["model"])
    if model.startswith("openrouter/") and not model.endswith(":free"):
        return f"{model}: OpenRouter model without the ':free' suffix"
    if dep.get("tier") == "paid":
        return f"{dep['name']}: tier is paid"
    return None


def build_backend(
    config: dict[str, Any],
    name: str,
    *,
    allow_synthetic_confidential: bool,
    env: Mapping[str, str] | None = None,
) -> tuple[Backend | None, float, str | None]:
    """``(backend, pause_seconds, skip_reason)``: skipped when the key is unset or the deployment is not free."""
    environ: Mapping[str, str] = os.environ if env is None else env
    dep = next(d for d in config["deployments"] if d["name"] == name)
    if (why := not_free_reason(dep)) is not None:
        return None, 0.0, why
    key_env = dep.get("api_key_env")
    if key_env and not environ.get(key_env):
        return None, 0.0, f"{key_env} is not set"
    from llmrouter_free import LLMRouter
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    one = {**config, "deployments": [dict(dep)], "routes": {"fast": [name], "reasoning": [name]}}
    router_config, policy = build_router_config(one, [], allow_any=allow_synthetic_confidential)
    ledger = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    router = LLMRouter(
        router_config,
        engine=ledger,
        completion_fn=MeteringCompletion(),
        allow_paid=False,
        max_usd_per_day=0.0,
    )
    attempts = Attempts()
    prices = load_price_table(Path(__file__).resolve().parents[2] / "config" / "llm_prices.yaml")
    llm = LlmRouterAdapter(
        router, prices, attempts, SystemClock(), UuidGenerator(),
        {ModelTier.FAST: "fast", ModelTier.REASONING: "reasoning"},
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="live-eval"), policy,
    )  # fmt: skip
    rpm = float(dep.get("rpm") or 20)
    return Backend(name, llm, attempts, allow_synthetic_confidential), 60.0 / rpm * 1.1, None
