"""Composition root: the only module that wires adapters into services.

Optional or heavy adapters (openai, agents, llmrouter_free, litellm) are imported inside their branch so that
the default install, tests and CI never import them.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol, assert_never, runtime_checkable

from scout.adapters.llm.fake_llm import FakeLLM
from scout.adapters.observability.analytics import BufferedAnalytics, NullAnalytics
from scout.adapters.observability.prices import load_price_table, resolve_path
from scout.adapters.observability.usage_meter import BufferedUsageMeter
from scout.adapters.persistence.db import Database
from scout.identity.adapters.dev_auth import DevKeyPair, DevTokenIssuer, DevVerifier
from scout.identity.adapters.jwks import HttpJwksFetcher, SupabaseJwksVerifier
from scout.identity.ports.auth import AuthTokenVerifier
from scout.ports.analytics import AnalyticsPort
from scout.ports.llm import LLMPort, ModelTier
from scout.ports.usage import PriceTable, UsageMeterPort
from scout.settings import Settings
from scout.shared import Clock, IdGenerator, SystemClock, UuidGenerator


@runtime_checkable
class Lifecycle(Protocol):
    """Components with background tasks."""

    async def start(self) -> None:
        """Start background tasks."""
        ...

    async def close(self) -> None:
        """Flush and stop."""
        ...


@dataclass(frozen=True, slots=True)
class Container:
    """Everything use cases need. Override fields in tests with ``dataclasses.replace``."""

    settings: Settings
    clock: Clock
    ids: IdGenerator
    db: Database
    prices: PriceTable
    usage_meter: UsageMeterPort
    analytics: AnalyticsPort
    llm: LLMPort
    auth_verifier: AuthTokenVerifier
    dev_tokens: DevTokenIssuer | None = None


def _select_llm(
    s: Settings, prices: PriceTable, usage: UsageMeterPort, clock: Clock, ids: IdGenerator
) -> LLMPort:
    match s.llm_backend:
        case "fake":
            return FakeLLM(resolve_path(s.llm_fixtures_dir), usage, prices, clock, ids)
        case "llm_router":
            from llmrouter_free import LLMRouter, load_routes
            from sqlalchemy import create_engine
            from sqlalchemy.pool import StaticPool

            from scout.adapters.llm.llm_router import LlmRouterAdapter, MeteringCompletion

            # The router's ledger stores response text. Keep it in process memory only: nothing vault-derived
            # reaches disk outside our database (DECISIONS 23).
            ledger = create_engine(
                "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
            )
            router = LLMRouter(
                load_routes(resolve_path(s.llm_router_config)),
                engine=ledger,
                completion_fn=MeteringCompletion(),
                allow_paid=s.llm_router_allow_paid,
                max_usd_per_day=s.llm_router_max_usd_per_day,
            )
            return LlmRouterAdapter(
                router, prices, usage, clock, ids,
                {ModelTier.FAST: s.llm_model_fast, ModelTier.REASONING: s.llm_model_reasoning},
                ThreadPoolExecutor(max_workers=s.llm_max_concurrency, thread_name_prefix="llm"),
            )  # fmt: skip
        case "openai_direct":
            from scout.adapters.llm.openai_direct import OpenAIAgentsDirectAdapter

            return OpenAIAgentsDirectAdapter(
                prices, usage, clock, ids,
                {ModelTier.FAST: s.llm_model_fast, ModelTier.REASONING: s.llm_model_reasoning},
            )  # fmt: skip
        case _ as unreachable:
            assert_never(unreachable)


def _select_auth(s: Settings, clock: Clock) -> tuple[AuthTokenVerifier, DevTokenIssuer | None]:
    match s.auth_mode:
        case "dev":
            keys = DevKeyPair.load_or_create(resolve_path(s.dev_keys_dir))
            return DevVerifier(keys, s.dev_issuer, clock), DevTokenIssuer(keys, s.dev_issuer, clock)
        case "supabase":
            assert s.jwks_url and s.supabase_issuer  # guaranteed by Settings validation
            return SupabaseJwksVerifier(HttpJwksFetcher(s.jwks_url), s.supabase_issuer, clock), None
        case _ as unreachable:
            assert_never(unreachable)


def build_container(
    settings: Settings, *, clock: Clock | None = None, ids: IdGenerator | None = None
) -> Container:
    """Create the container. Opens no connection and starts no background task."""
    clock, ids = clock or SystemClock(), ids or UuidGenerator()
    db = Database(settings.database_url)
    prices = load_price_table(settings.llm_price_table)
    usage: UsageMeterPort = BufferedUsageMeter(db, settings.usage_flush_ms, settings.usage_queue_max)
    analytics: AnalyticsPort = (
        BufferedAnalytics(db, settings.analytics_flush_ms, settings.analytics_queue_max)
        if settings.analytics_enabled
        else NullAnalytics()
    )
    verifier, dev_tokens = _select_auth(settings, clock)
    return Container(
        auth_verifier=verifier, dev_tokens=dev_tokens,
        settings=settings, clock=clock, ids=ids, db=db, prices=prices, usage_meter=usage, analytics=analytics,
        llm=_select_llm(settings, prices, usage, clock, ids),
    )  # fmt: skip


def _lifecycles(c: Container) -> list[Lifecycle]:
    return [x for x in (c.usage_meter, c.analytics) if isinstance(x, Lifecycle)]


async def start_container(container: Container) -> None:
    """Start background writers."""
    for component in _lifecycles(container):
        await component.start()


async def close_container(container: Container) -> None:
    """Flush buffers (before the engine goes away), then dispose the engine."""
    for component in _lifecycles(container):
        await component.close()
    await container.db.dispose()
