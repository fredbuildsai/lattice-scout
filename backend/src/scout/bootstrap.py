"""Composition root: the only module that wires adapters into services.

Optional or heavy adapters (openai, agents, llmrouter_free, litellm) are imported inside their branch so that
the default install, tests and CI never import them.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol, assert_never, runtime_checkable
from uuid import UUID

import structlog

from scout.adapters.literature.fixtures import FixtureLiterature
from scout.adapters.llm.factory import AgentFactory
from scout.adapters.llm.fake_llm import FakeLLM
from scout.adapters.observability.analytics import BufferedAnalytics, NullAnalytics
from scout.adapters.observability.budget import GlobalBudget, SqlSpendReader
from scout.adapters.observability.prices import load_price_table, resolve_path
from scout.adapters.observability.usage_meter import BufferedUsageMeter
from scout.adapters.persistence.db import Database
from scout.adapters.persistence.erasure import (
    SqlAccountFinalizer,
    SqlObservabilityErasure,
    SqlStuckErasures,
    SqlSubjectInventory,
)
from scout.adapters.queue.postgres import PostgresJobQueue
from scout.adapters.storage.local import LocalFileStorage
from scout.discovery.adapters.agents import AgentsDiscovery
from scout.discovery.adapters.fields import load_field
from scout.discovery.adapters.sql import SqlDiscoveryUnitOfWork, SqlRunLedger
from scout.discovery.application.pipeline import PipelineConfig
from scout.discovery.application.run_analysis import RunAnalysis, SweepRuns
from scout.discovery.application.service import DiscoveryService
from scout.discovery.application.sources import ProfileSourceAdapter, VaultSourceAdapter
from scout.discovery.application.start import StartAnalysisRun, StartConfig
from scout.discovery.ports.repos import DiscoveryUnitOfWork
from scout.discovery.ports.sources import VaultSource
from scout.feedback.adapters.sql import SqlFeedbackUnitOfWork
from scout.feedback.application.give_feedback import GiveFeedback
from scout.feedback.application.targets import TargetCheckAdapter
from scout.identity.adapters.auth_admin import DevAuthAdmin, SupabaseAuthAdmin
from scout.identity.adapters.dev_auth import DevKeyPair, DevTokenIssuer, DevVerifier
from scout.identity.adapters.jwks import HttpJwksFetcher, SupabaseJwksVerifier
from scout.identity.adapters.sql import SqlIdentityUnitOfWork
from scout.identity.application.erasure import DeleteAccountJob, SweepErasures
from scout.identity.application.service import IdentityService
from scout.identity.ports.auth import AuthTokenVerifier
from scout.matching.adapters.agents import AgentsMatching
from scout.matching.adapters.sql import SqlMatchUnitOfWork
from scout.matching.adapters.vocabulary import load_vocabulary
from scout.matching.application.compute import ComputeMatches
from scout.matching.application.config import MatchConfig
from scout.matching.application.erasure import MatchErasure
from scout.matching.application.profile_job import ComputeResearcherProfile
from scout.matching.application.reveal_job import MatchingNightly, RevealMatch
from scout.matching.application.service import DecideOnMatch, MatchingService
from scout.matching.application.sources import ConsentSourceAdapter, VaultSignalAdapter
from scout.matching.application.sources import ProfileSourceAdapter as MatchProfileSource
from scout.notifications.adapters.senders import ConsoleEmailSender, ResendEmailSender
from scout.notifications.adapters.sql import SqlNotificationStore
from scout.notifications.application.service import MatchMailNotifier, Notifications, WeeklyDigest
from scout.notifications.domain.notification import Template
from scout.ports.analytics import AnalyticsPort
from scout.ports.budget import GlobalBudgetPort
from scout.ports.jobs import JobQueuePort
from scout.ports.literature import LiteraturePort
from scout.ports.llm import LLMPort, ModelTier
from scout.ports.storage import FileStoragePort
from scout.ports.usage import PriceTable, UsageMeterPort
from scout.profile.adapters.sql import SqlProfileUnitOfWork
from scout.profile.application.import_profile import ImportProfile
from scout.profile.application.service import ProfileConfig, ProfileService
from scout.settings import Settings
from scout.shared import Clock, IdGenerator, SystemClock, UuidGenerator
from scout.vault.adapters.extraction import PdfDocxExtractor
from scout.vault.adapters.insight_extractor import AgentsInsightExtractor
from scout.vault.adapters.sql import SqlVaultUnitOfWork
from scout.vault.application.delete_job import DeleteDocumentJob
from scout.vault.application.extract_insights import ExtractInsights
from scout.vault.application.ingest import IngestDocument
from scout.vault.application.service import VaultService
from scout.vault.application.sweep import SweepVault
from scout.vault.application.upload import UploadDocument
from scout.vault.domain.enums import RetentionPolicy


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
    literature: LiteraturePort
    job_queue: JobQueuePort
    identity: IdentityService
    delete_account: DeleteAccountJob
    global_budget: GlobalBudgetPort
    notifications: Notifications
    weekly_digest: WeeklyDigest
    sweep_erasures: SweepErasures
    profile: ProfileService
    import_profile: ImportProfile
    storage: FileStoragePort
    agents: AgentFactory
    vault: VaultService
    upload_document: UploadDocument
    ingest_document: IngestDocument
    delete_document_job: DeleteDocumentJob
    sweep_vault: SweepVault
    start_run: StartAnalysisRun
    run_analysis: RunAnalysis
    sweep_runs: SweepRuns
    discovery: DiscoveryService
    discovery_uow: Callable[[], DiscoveryUnitOfWork]
    vault_source: VaultSource
    give_feedback: GiveFeedback
    matching: MatchingService
    decide_match: DecideOnMatch
    match_profile: ComputeResearcherProfile
    compute_matches: ComputeMatches
    reveal_match: RevealMatch
    match_nightly: MatchingNightly
    dev_tokens: DevTokenIssuer | None = None


def _select_llm(
    s: Settings, prices: PriceTable, usage: UsageMeterPort, clock: Clock, ids: IdGenerator
) -> LLMPort:
    match s.llm_backend:
        case "fake":
            fake = FakeLLM(resolve_path(s.llm_fixtures_dir), usage, prices, clock, ids)
            fake.confidential_ok = s.llm_fake_confidential_allowed
            return fake
        case "llm_router":
            import os

            from llmrouter_free import LLMRouter, load_routes
            from sqlalchemy import create_engine
            from sqlalchemy.pool import StaticPool

            from scout.adapters.llm.llm_router import LlmRouterAdapter, MeteringCompletion

            # llm-router reads provider keys from the process environment; .env is only read by Settings.
            for name, key in (
                ("OPENROUTER_API_KEY", s.openrouter_api_key), ("GROQ_API_KEY", s.groq_api_key),
                ("MISTRAL_API_KEY", s.mistral_api_key),
            ):  # fmt: skip
                if key is not None:
                    os.environ.setdefault(name, key.get_secret_value())
            # The router's ledger stores response text. Keep it in process memory only: nothing vault-derived
            # reaches disk outside our database (DECISIONS 23).
            ledger = create_engine(
                "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
            )
            from scout.adapters.llm.confidential_policy import build_router_config

            router_config, policy = build_router_config(
                load_routes(resolve_path(s.llm_router_config)),
                s.confidential_authorized_providers,
                allow_any=s.llm_confidential_allow_any_provider,
            )
            router = LLMRouter(
                router_config,
                engine=ledger,
                completion_fn=MeteringCompletion(),
                allow_paid=s.llm_router_allow_paid,
                max_usd_per_day=s.llm_router_max_usd_per_day,
            )
            return LlmRouterAdapter(
                router, prices, usage, clock, ids,
                {ModelTier.FAST: s.llm_model_fast, ModelTier.REASONING: s.llm_model_reasoning},
                ThreadPoolExecutor(max_workers=s.llm_max_concurrency, thread_name_prefix="llm"),
                policy,
            )  # fmt: skip
        case "openai_direct":
            from scout.adapters.llm.confidential_policy import ConfidentialPolicy
            from scout.adapters.llm.openai_direct import OpenAIAgentsDirectAdapter

            return OpenAIAgentsDirectAdapter(
                prices, usage, clock, ids,
                {ModelTier.FAST: s.llm_model_fast, ModelTier.REASONING: s.llm_model_reasoning},
                ConfidentialPolicy(
                    allow_any=s.llm_confidential_allow_any_provider,
                    authorized_providers=s.confidential_authorized_providers,
                ),
            )  # fmt: skip
        case _ as unreachable:
            assert_never(unreachable)


def _select_literature(
    s: Settings, db: Database, clock: Clock, ids: IdGenerator, usage: UsageMeterPort
) -> LiteraturePort:
    match s.literature_backend:
        case "fixtures":
            return FixtureLiterature(resolve_path(s.literature_fixtures_dir))
        case "openalex":
            import httpx

            from scout.adapters.literature.cache import PostgresOpenAlexCache
            from scout.adapters.literature.openalex import OpenAlexClient

            return OpenAlexClient(
                httpx.AsyncClient(timeout=30), clock, ids, usage,
                api_key=s.openalex_api_key.get_secret_value() if s.openalex_api_key else None,
                mailto=s.openalex_mailto, cache=PostgresOpenAlexCache(db, clock),
            )  # fmt: skip
        case _ as unreachable:
            assert_never(unreachable)


def _select_storage(s: Settings, usage: UsageMeterPort, clock: Clock, ids: IdGenerator) -> FileStoragePort:
    match s.storage_backend:
        case "local":
            return LocalFileStorage(resolve_path(s.local_storage_dir))
        case "supabase":
            import httpx

            from scout.adapters.storage.supabase import SupabaseStorage

            assert s.supabase_url and s.supabase_service_role_key  # guaranteed by Settings validation
            return SupabaseStorage(
                httpx.AsyncClient(), s.supabase_url, s.supabase_service_role_key.get_secret_value(),
                s.supabase_storage_bucket, usage, clock, ids,
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


def _select_email(s: Settings) -> ConsoleEmailSender | ResendEmailSender:
    if s.email_provider == "resend" and s.email_api_key and s.email_from:
        import httpx

        return ResendEmailSender(httpx.AsyncClient(), s.email_api_key.get_secret_value(), s.email_from)
    return ConsoleEmailSender()


def _select_auth_admin(s: Settings) -> DevAuthAdmin | SupabaseAuthAdmin:
    if (
        s.auth_mode == "supabase" and s.supabase_url and s.supabase_service_role_key
    ):  # prod needs both (storage)
        import httpx

        return SupabaseAuthAdmin(
            httpx.AsyncClient(), s.supabase_url, s.supabase_service_role_key.get_secret_value()
        )
    return DevAuthAdmin()


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
    literature = _select_literature(settings, db, clock, ids, usage)
    queue = PostgresJobQueue(db, clock, ids, lease_seconds=settings.job_lease_seconds)
    cfg = ProfileConfig()
    llm = _select_llm(settings, prices, usage, clock, ids)
    storage = _select_storage(settings, usage, clock, ids)
    agents = AgentFactory(
        llm, resolve_path(settings.prompts_dir),
        {"field_name": settings.launch_field},
    )  # fmt: skip
    vault_uow = lambda: SqlVaultUnitOfWork(db)  # noqa: E731
    extract_insights = ExtractInsights(
        AgentsInsightExtractor(agents), settings.insight_full_max_chars, settings.insight_slice_chars,
        settings.insight_map_concurrency,
    )  # fmt: skip
    extractor = PdfDocxExtractor(
        timeout_s=settings.vault_extract_timeout_s, memory_mb=settings.vault_extract_memory_mb,
        max_pages=settings.vault_max_pages, max_chars=settings.vault_max_text_chars,
    )  # fmt: skip
    discovery_uow = lambda: SqlDiscoveryUnitOfWork(db)  # noqa: E731
    vault_service = VaultService(vault_uow, queue, clock, ids)
    profile_service = ProfileService(lambda: SqlProfileUnitOfWork(db), literature, queue, clock, ids, cfg)
    profile_source, vault_source = ProfileSourceAdapter(profile_service), VaultSourceAdapter(vault_service)
    global_budget = GlobalBudget(
        SqlSpendReader(db),
        clock,
        settings.global_daily_llm_budget_usd,
        settings.global_daily_openalex_budget_usd,
        llm_calls_cap=settings.global_daily_llm_calls,
    )
    start_run = StartAnalysisRun(
        discovery_uow, queue, profile_source, vault_source, clock, ids,
        StartConfig(
            settings.run_budget_usd, settings.account_daily_budget_usd, settings.run_max_per_day,
            settings.run_max_openalex_calls, settings.run_max_corpus,
        ),
        global_budget,
    )  # fmt: skip
    match_uow = lambda: SqlMatchUnitOfWork(db)  # noqa: E731
    vocabulary = load_vocabulary(resolve_path(settings.methods_config))
    match_cfg = MatchConfig(
        field=settings.launch_field, expiry_days=settings.match_expiry_days, cooldown_days=settings.match_cooldown_days,
        weekly_cap=settings.match_max_open_per_week, min_score=settings.match_min_score, alpha=settings.match_alpha,
        beta=settings.match_beta, exclude_coauthors=settings.match_exclude_coauthors,
        exclude_same_group=settings.match_exclude_same_group, max_explains_per_run=settings.match_max_explains_per_run,
        nightly_utc_hour=settings.match_nightly_utc_hour,
    )  # fmt: skip
    audit_key = settings.audit_hash_key.get_secret_value()
    identity_service = IdentityService(
        lambda: SqlIdentityUnitOfWork(db), clock, ids, settings.launch_field, queue, audit_key
    )
    delete_account = DeleteAccountJob(
        inventory=SqlSubjectInventory(db, audit_key), matching=MatchErasure(match_uow), storage=storage,
        auth_admin=_select_auth_admin(settings), observability=SqlObservabilityErasure(db),
        finalizer=SqlAccountFinalizer(db), jobs=queue, clock=clock, ids=ids, audit_key=audit_key,
        second_pass_minutes=settings.erasure_second_pass_minutes,
    )  # fmt: skip
    notif_store = SqlNotificationStore(db)
    notifications = Notifications(notif_store, _select_email(settings), queue, clock, settings.app_base_url)
    match_agents, match_notifier = AgentsMatching(agents), MatchMailNotifier(notifications)
    consent_source, match_profile_source = (
        ConsentSourceAdapter(identity_service),
        MatchProfileSource(profile_service),
    )
    signal_source = VaultSignalAdapter(vault_service)
    profile_job = ComputeResearcherProfile(
        match_uow,
        match_profile_source,
        consent_source,
        signal_source,
        match_agents,
        vocabulary,
        queue,
        clock,
        match_cfg,
    )
    compute_matches = ComputeMatches(match_uow, match_agents, vocabulary, match_notifier, clock, match_cfg)

    async def run_done(owner: UUID, run_id: UUID) -> None:
        await notifications.notify(owner, Template.RUN_FINISHED, str(run_id))

    matching_service = MatchingService(match_uow, consent_source, clock, match_cfg)
    discovery_service = DiscoveryService(
        discovery_uow, start_run, vault_source, clock, ids,
        admin_show_vault_quotes=settings.admin_claims_show_vault_quotes,
    )  # fmt: skip

    for event, level, detail in settings.confidential_startup_events(llm.confidential_allowed()):
        getattr(structlog.get_logger("scout.startup"), level)(event, detail=detail)
    return Container(
        matching=matching_service,
        decide_match=DecideOnMatch(match_uow, consent_source, queue, match_notifier, clock, match_cfg),
        match_profile=profile_job, compute_matches=compute_matches,
        reveal_match=RevealMatch(match_uow, match_profile_source, consent_source, signal_source, match_agents, vocabulary, clock),
        match_nightly=MatchingNightly(match_uow, queue, profile_job, compute_matches, clock, match_cfg, global_budget),
        storage=storage, agents=agents,
        vault=vault_service,
        upload_document=UploadDocument(
            vault_uow, storage, queue, clock, ids, RetentionPolicy(settings.retention_policy),
            settings.vault_max_bytes, settings.vault_max_documents, settings.upload_max_per_day,
        ),
        ingest_document=IngestDocument(vault_uow, storage, extractor, extract_insights, clock),
        delete_document_job=DeleteDocumentJob(vault_uow, storage, clock),
        sweep_vault=SweepVault(vault_uow, queue, clock, provider_ready=extract_insights.available),
        start_run=start_run,
        run_analysis=RunAnalysis(
            discovery_uow, SqlRunLedger(db, clock), literature, AgentsDiscovery(agents), profile_source,
            vault_source, load_field(resolve_path(settings.fields_config), settings.launch_field),
            PipelineConfig(
                settings.run_llm_concurrency, settings.run_openalex_concurrency, settings.run_analyse_max_calls,
                max_resolve=settings.run_max_resolve,
            ),
            clock,
            run_done,
        ),
        sweep_runs=SweepRuns(
            discovery_uow, queue, clock, stall_minutes=settings.run_stall_minutes,
            max_executions=settings.run_max_executions,
        ),
        discovery=discovery_service,
        discovery_uow=discovery_uow, vault_source=vault_source,
        give_feedback=GiveFeedback(
            lambda: SqlFeedbackUnitOfWork(db), clock, TargetCheckAdapter(discovery_service, matching_service)
        ),

        auth_verifier=verifier, dev_tokens=dev_tokens,
        literature=literature,
        job_queue=queue,
        identity=identity_service,
        delete_account=delete_account,
        global_budget=global_budget,
        notifications=notifications,
        weekly_digest=WeeklyDigest(notif_store, notifications, queue, clock),
        sweep_erasures=SweepErasures(SqlStuckErasures(db), queue, clock, audit_key),
        profile=profile_service,
        import_profile=ImportProfile(lambda: SqlProfileUnitOfWork(db), literature, clock, ids, cfg),
        settings=settings, clock=clock, ids=ids, db=db, prices=prices, usage_meter=usage, analytics=analytics,
        llm=llm,
    )  # fmt: skip


def _lifecycles(c: Container) -> list[Lifecycle]:
    return [x for x in (c.usage_meter, c.analytics, c.literature) if isinstance(x, Lifecycle)]


async def start_container(container: Container) -> None:
    """Start background writers."""
    for component in _lifecycles(container):
        await component.start()


async def close_container(container: Container) -> None:
    """Flush buffers (before the engine goes away), then dispose the engine."""
    for component in _lifecycles(container):
        await component.close()
    await container.db.dispose()
