"""Application settings (environment variables / .env). Only entry points and bootstrap read them."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration, with offline-safe defaults (fake LLM, fixtures, local storage, dev auth)."""

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore", case_sensitive=False)

    app_env: Literal["test", "dev", "prod"] = "dev"
    database_url: str = "postgresql+psycopg://scout:scout@localhost:5432/scout"
    test_database_url: str | None = None

    llm_backend: Literal["fake", "llm_router", "openai_direct"] = "fake"
    literature_backend: Literal["fixtures", "openalex"] = "fixtures"
    storage_backend: Literal["local", "supabase"] = "local"
    email_provider: Literal["console", "resend"] = "console"
    auth_mode: Literal["dev", "supabase"] = "dev"
    dev_keys_dir: Path = Path("var/dev-keys")
    dev_issuer: str = "http://localhost:8000/dev-auth"
    local_storage_dir: Path = Path("var/storage")
    audit_hash_key: SecretStr = SecretStr("dev-audit-hash-key-change-me")  # pseudonymises erased accounts
    erasure_second_pass_minutes: int = 15

    supabase_url: str | None = None
    supabase_publishable_key: str | None = None
    supabase_service_role_key: SecretStr | None = None
    supabase_jwks_url: str | None = None

    openai_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None  # read by llm-router (config/llm_router.yaml)
    groq_api_key: SecretStr | None = None
    mistral_api_key: SecretStr | None = None
    llm_router_config: Path = Path("config/llm_router.yaml")
    llm_router_source: str | None = None
    llm_model_reasoning: str = "reasoning"
    llm_model_fast: str = "fast"
    llm_price_table: Path = Path("config/llm_prices.yaml")

    openalex_api_key: SecretStr | None = None
    openalex_mailto: str | None = None
    literature_fixtures_dir: Path = Path("tests/fixtures/openalex")
    launch_field: str = "bio-based-materials"

    run_budget_usd: Decimal = Decimal("1.50")
    run_max_openalex_calls: int = Field(default=400, ge=1, le=400)
    run_max_corpus: int = 2500
    run_max_per_day: int = 5
    account_daily_budget_usd: Decimal = Decimal("5.00")
    global_daily_llm_budget_usd: Decimal = Decimal("20.00")  # all users together; reached = new runs pause
    global_daily_openalex_budget_usd: Decimal = Decimal("5.00")
    # Free models cost 0 USD, so the USD cap cannot trip on them; this caps provider requests per UTC day
    # (every attempt counts). 0 = off. Set it below the providers' combined free daily quota.
    global_daily_llm_calls: int = 1500
    upload_max_per_day: int = 30  # per account, 0 = unlimited
    run_llm_concurrency: int = 3
    run_openalex_concurrency: int = 4
    run_analyse_max_calls: int = 2
    run_max_resolve: int = 20
    run_stall_minutes: int = 20
    run_max_executions: int = 8
    fields_config: Path = Path("config/fields.yaml")
    admin_claims_show_vault_quotes: bool = False
    methods_config: Path = Path("config/methods.yaml")
    match_max_open_per_week: int = 3
    match_expiry_days: int = 14
    match_cooldown_days: int = 180
    match_min_score: float = 0.08
    match_alpha: float = 1.0
    match_beta: float = 1.5
    match_exclude_coauthors: bool = True
    match_exclude_same_group: bool = True
    match_max_explains_per_run: int = 200
    match_nightly_utc_hour: int = 2
    match_non_members: bool = False
    retention_policy: Literal["keep", "discard", "user_choice"] = "user_choice"

    analytics_enabled: bool = True
    analytics_retention_months: int = (
        13  # raw analytics, LLM and external-call rows older than this are dropped
    )
    analytics_flush_ms: int = 2000
    analytics_retention_days: int = 395
    usage_flush_ms: int = 2000
    otel_exporter_otlp_endpoint: str | None = None
    otel_traces_sampler_arg: float = 0.2

    email_api_key: SecretStr | None = None
    email_from: str | None = None
    app_base_url: str = "http://localhost:5173"  # links in e-mails
    worker_poll_interval_ms: int = 1000
    job_lease_seconds: int = 300

    vault_max_bytes: int = 26_214_400
    vault_max_pages: int = 300
    vault_max_text_chars: int = 720_000
    vault_extract_timeout_s: int = 60
    vault_extract_memory_mb: int = 1024
    vault_max_documents: int = 50
    insight_full_max_chars: int = 120_000
    insight_slice_chars: int = 60_000
    insight_map_concurrency: int = 3
    supabase_storage_bucket: str = "vault"

    llm_fixtures_dir: Path = Path("backend/tests/fixtures/llm")
    prompts_dir: Path = Path("prompts")
    llm_max_concurrency: int = 8
    llm_router_allow_paid: bool = False  # free providers only
    # Prod refuses llm_router (free-tier OpenRouter/Groq/Mistral) until the owner confirms the providers' data-use
    # terms and opt-outs (docs/adr/015, docs/BLOCKERS.md). Default off.
    llm_free_providers_acknowledged: bool = False
    # Vault data (documents, passages, insights, profile text from them) goes only to deployments that are marked
    # ``sanctioned: true`` in llm_router.yaml AND whose provider is listed here, e.g. "mistral,groq". Empty = no
    # remote endpoint is sanctioned (only the fake backend can handle vault-tagged calls). ADR 015.
    llm_vault_sanctioned_providers: str = ""
    llm_vault_allow_any_provider: bool = (
        False  # explicit waiver: vault data may reach any configured deployment
    )
    llm_router_max_usd_per_day: float = 5.0
    analytics_queue_max: int = 20000
    usage_queue_max: int = 10000
    otel_service_name: str | None = None
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    embedding_model: str | None = None
    embedding_dim: int | None = None

    @property
    def vault_sanctioned_providers(self) -> frozenset[str]:
        """Parsed ``LLM_VAULT_SANCTIONED_PROVIDERS``."""
        return frozenset(
            p.strip().lower() for p in self.llm_vault_sanctioned_providers.split(",") if p.strip()
        )

    @property
    def supabase_issuer(self) -> str | None:
        """Expected ``iss`` of Supabase access tokens."""
        return f"{self.supabase_url.rstrip('/')}/auth/v1" if self.supabase_url else None

    @property
    def jwks_url(self) -> str | None:
        """JWKS endpoint: explicit setting or derived from the Supabase URL."""
        if self.supabase_jwks_url:
            return self.supabase_jwks_url
        if self.supabase_url:
            return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        return None

    @model_validator(mode="after")
    def _check_backends(self) -> Self:
        problems: list[str] = []
        if self.app_env == "prod":
            if self.auth_mode == "dev":
                problems.append("AUTH_MODE=dev is refused when APP_ENV=prod")
            if self.llm_backend == "fake":
                problems.append("LLM_BACKEND=fake is refused when APP_ENV=prod")
            if self.literature_backend == "fixtures":
                problems.append("LITERATURE_BACKEND=fixtures is refused when APP_ENV=prod")
            if self.email_provider == "console":
                problems.append("EMAIL_PROVIDER=console is refused when APP_ENV=prod")
            if self.storage_backend == "local":
                problems.append("STORAGE_BACKEND=local is refused when APP_ENV=prod")
            if "*" in self.cors_origins or any(o.startswith("http://") for o in self.cors_origins):
                problems.append("CORS_ORIGINS must be https origins without '*' when APP_ENV=prod")
            if not self.app_base_url.startswith("https://") or "localhost" in self.app_base_url:
                problems.append("APP_BASE_URL must be an https URL (not localhost) when APP_ENV=prod")
            if self.audit_hash_key.get_secret_value() == "dev-audit-hash-key-change-me":
                problems.append("AUDIT_HASH_KEY must be set when APP_ENV=prod")
        if self.storage_backend == "supabase" and not (self.supabase_url and self.supabase_service_role_key):
            problems.append("STORAGE_BACKEND=supabase needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
        if self.auth_mode == "supabase" and not (self.jwks_url and self.supabase_url):
            problems.append("AUTH_MODE=supabase needs SUPABASE_URL (token issuer) and a JWKS URL")
        if (
            self.app_env == "prod"
            and self.llm_backend == "llm_router"
            and not self.llm_free_providers_acknowledged
        ):
            problems.append(
                "LLM_BACKEND=llm_router sends text to free-tier providers; set "
                "LLM_FREE_PROVIDERS_ACKNOWLEDGED=true only after confirming their data-use terms (docs/adr/015)"
            )
        if (
            self.app_env == "prod"
            and self.llm_backend in ("llm_router", "openai_direct")
            and not (self.vault_sanctioned_providers or self.llm_vault_allow_any_provider)
        ):
            problems.append(
                "vault-tagged agents exist but LLM_VAULT_SANCTIONED_PROVIDERS is empty; list the providers whose "
                "terms you confirmed, or set LLM_VAULT_ALLOW_ANY_PROVIDER=true to waive the rule (docs/adr/015)"
            )
        if self.llm_backend == "llm_router" and not (
            self.openrouter_api_key or self.groq_api_key or self.mistral_api_key
        ):
            problems.append(
                "LLM_BACKEND=llm_router needs at least one provider key (OPENROUTER, GROQ, MISTRAL)"
            )
        if self.llm_backend == "openai_direct" and not self.openai_api_key:
            problems.append("LLM_BACKEND=openai_direct needs OPENAI_API_KEY")
        if self.email_provider == "resend" and not (self.email_api_key and self.email_from):
            problems.append("EMAIL_PROVIDER=resend needs EMAIL_API_KEY and EMAIL_FROM")
        if problems:
            raise ValueError("; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached settings for entry points."""
    return Settings()
