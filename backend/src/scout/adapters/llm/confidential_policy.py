"""Which routes and models may carry confidential data (ADR 016, superseding ADR 015's refusal rule).

A deployment is *authorized* for confidential data when it is marked ``authorized: true`` in the route
configuration (``sanctioned: true`` is read as a legacy alias) **and** its provider is on the
``LLM_CONFIDENTIAL_AUTHORIZED_PROVIDERS`` allowlist (or the operator set ``LLM_CONFIDENTIAL_ALLOW_ANY_PROVIDER``).
Published data may use every deployment. The adapter reports ``allows_confidential(tier)``; the agent factory then
withholds confidential parts of the input on routes that are not authorized, so the work goes ahead on published data.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from scout.adapters.llm.metering import provider_of
from scout.ports.llm import ConfidentialDataRefused, DataClass

_log = logging.getLogger("scout.llm.confidential_policy")
CONFIDENTIAL_SUFFIX = "__confidential"


@dataclass(frozen=True)
class ConfidentialPolicy:
    """Which routes and models are authorized for confidential data."""

    allow_any: bool = False
    authorized_providers: frozenset[str] = frozenset()
    confidential_routes: Mapping[str, str] = field(
        default_factory=dict
    )  # route -> same route restricted to authorized deployments
    authorized_models: frozenset[str] = frozenset()

    def allows_confidential(self, route: str) -> bool:
        """Whether ``route`` has authorized deployments only available (every fallback of the request is authorized)."""
        return self.allow_any or route in self.confidential_routes

    def route_for(self, agent_name: str, data_class: DataClass, route: str) -> str:
        """The route to use. A confidential call is pinned to authorized deployments only, so a fall-over can
        never reach an unauthorized one; raises ``ConfidentialDataRefused`` (no content) when there is none."""
        if data_class is not DataClass.CONFIDENTIAL or self.allow_any:
            return route
        pinned = self.confidential_routes.get(route)
        if pinned is None:
            _log.error(
                "confidential_refused agent=%s route=%s reason=no_authorized_deployment", agent_name, route
            )
            raise ConfidentialDataRefused(f"No authorized deployment for confidential data on route {route}")
        return pinned

    def check_model(self, model: str) -> None:
        """Last line of defence, called before each provider request of a confidential call."""
        if self.allow_any or model in self.authorized_models:
            return
        _log.error("confidential_refused model_provider=%s reason=unauthorized_model", provider_of(model))
        raise ConfidentialDataRefused("Confidential data refused for an unauthorized model")

    def provider_allowed(self, provider: str) -> bool:
        """For adapters without routes (direct OpenAI)."""
        return self.allow_any or provider in self.authorized_providers


def build_router_config(
    config: Mapping[str, Any], authorized_providers: Iterable[str], *, allow_any: bool = False
) -> tuple[dict[str, Any], ConfidentialPolicy]:
    """Return the config for ``LLMRouter`` (with extra ``<route>__confidential`` routes) and the matching policy.

    The ``authorized`` / ``sanctioned`` keys are removed from deployments (the router does not know them).
    """
    providers = frozenset(p.lower() for p in authorized_providers)
    deployments = [dict(d) for d in config.get("deployments", [])]
    ok: set[str] = set()
    models: set[str] = set()
    for d in deployments:
        marked = bool(d.pop("authorized", False)) | bool(d.pop("sanctioned", False))
        if marked and provider_of(str(d["model"])) in providers:
            ok.add(str(d["name"]))
            models.add(str(d["model"]))
    routes: dict[str, list[str]] = {k: list(v) for k, v in config.get("routes", {}).items()}
    pinned: dict[str, str] = {}
    for name, members in list(routes.items()):
        allowed = [m for m in members if m in ok]
        if allowed:
            routes[name + CONFIDENTIAL_SUFFIX] = allowed
            pinned[name] = name + CONFIDENTIAL_SUFFIX
    out = {**config, "deployments": deployments, "routes": routes}
    return out, ConfidentialPolicy(allow_any, providers, pinned, frozenset(models))
