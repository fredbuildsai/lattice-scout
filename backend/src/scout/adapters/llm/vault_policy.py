"""Vault-data policy for LLM endpoints (ADR 015).

A call tagged ``DataClass.VAULT`` may only reach a *sanctioned* deployment: one marked ``sanctioned: true`` in the
route configuration **and** whose provider is on the ``LLM_VAULT_SANCTIONED_PROVIDERS`` allowlist (or any
deployment, when the operator set ``LLM_VAULT_ALLOW_ANY_PROVIDER``). Public calls may use every deployment.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from scout.adapters.llm.metering import provider_of
from scout.ports.llm import DataClass, VaultDataRefused

_log = logging.getLogger("scout.llm.vault_policy")
VAULT_SUFFIX = "__vault"


@dataclass(frozen=True)
class VaultPolicy:
    """Which routes and models may carry vault data."""

    allow_any: bool = False
    sanctioned_providers: frozenset[str] = frozenset()
    vault_routes: Mapping[str, str] = field(
        default_factory=dict
    )  # route -> route with sanctioned deployments only
    sanctioned_models: frozenset[str] = frozenset()

    def route_for(self, agent_name: str, data_class: DataClass, route: str) -> str:
        """The route to use; raises ``VaultDataRefused`` (without any content) when a vault call has none."""
        if data_class is not DataClass.VAULT or self.allow_any:
            return route
        vault_route = self.vault_routes.get(route)
        if vault_route is None:
            _log.error(
                "vault_data_refused agent=%s route=%s reason=no_sanctioned_deployment", agent_name, route
            )
            raise VaultDataRefused(f"No sanctioned deployment for vault data on route {route}")
        return vault_route

    def check_model(self, model: str) -> None:
        """Last line of defence, called before each provider request of a vault call."""
        if self.allow_any or model in self.sanctioned_models:
            return
        _log.error("vault_data_refused model_provider=%s reason=unsanctioned_model", provider_of(model))
        raise VaultDataRefused("Vault data refused for an unsanctioned model")

    def provider_allowed(self, provider: str) -> bool:
        """For adapters without routes (direct OpenAI)."""
        return self.allow_any or provider in self.sanctioned_providers


def build_router_config(
    config: Mapping[str, Any], sanctioned_providers: Iterable[str], *, allow_any: bool = False
) -> tuple[dict[str, Any], VaultPolicy]:
    """Return the config for ``LLMRouter`` (with extra ``<route>__vault`` routes) and the matching policy.

    The ``sanctioned`` key is removed from deployments (the router does not know it).
    """
    providers = frozenset(p.lower() for p in sanctioned_providers)
    deployments = [dict(d) for d in config.get("deployments", [])]
    ok: set[str] = set()
    models: set[str] = set()
    for d in deployments:
        marked = bool(d.pop("sanctioned", False))
        if marked and provider_of(str(d["model"])) in providers:
            ok.add(str(d["name"]))
            models.add(str(d["model"]))
    routes: dict[str, list[str]] = {k: list(v) for k, v in config.get("routes", {}).items()}
    vault_routes: dict[str, str] = {}
    for name, members in list(routes.items()):
        allowed = [m for m in members if m in ok]
        if allowed:
            routes[name + VAULT_SUFFIX] = allowed
            vault_routes[name] = name + VAULT_SUFFIX
    out = {**config, "deployments": deployments, "routes": routes}
    return out, VaultPolicy(allow_any, providers, vault_routes, frozenset(models))
