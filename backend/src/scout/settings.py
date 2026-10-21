"""Application settings (environment variables / .env). Only entry points and bootstrap read them."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, model_validator
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

    supabase_url: str | None = None
    supabase_publishable_key: str | None = None
    supabase_service_role_key: SecretStr | None = None
    supabase_jwks_url: str | None = None

    openai_api_key: SecretStr | None = None
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
    run_max_openalex_calls: int = 400
    run_max_corpus: int = 2500
    run_max_per_day: int = 5
    account_daily_budget_usd: Decimal = Decimal("5.00")
    run_llm_concurrency: int = 3
    run_openalex_concurrency: int = 4
    run_analyse_max_calls: int = 2
    run_max_resolve: int = 20
    run_stall_minutes: int = 20
    run_max_executions: int = 8
    fields_config: Path = Path("config/fields.yaml")
    admin_claims_show_vault_quotes: bool = False
    match_max_open_per_week: int = 3
    match_expiry_days: int = 21
    match_exclude_coauthors: bool = True
    match_non_members: bool = False
    retention_policy: Literal["keep", "discard", "user_choice"] = "user_choice"

    analytics_enabled: bool = True
    analytics_flush_ms: int = 2000
    analytics_retention_days: int = 395
    usage_flush_ms: int = 2000
    otel_exporter_otlp_endpoint: str | None = None
    otel_traces_sampler_arg: float = 0.2

    email_api_key: SecretStr | None = None
    email_from: str | None = None
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
    llm_router_allow_paid: bool = True
    llm_router_max_usd_per_day: float = 5.0
    analytics_queue_max: int = 20000
    usage_queue_max: int = 10000
    otel_service_name: str | None = None
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    embedding_model: str | None = None
    embedding_dim: int | None = None

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
        if self.storage_backend == "supabase" and not (self.supabase_url and self.supabase_service_role_key):
            problems.append("STORAGE_BACKEND=supabase needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
        if self.auth_mode == "supabase" and not (self.jwks_url and self.supabase_url):
            problems.append("AUTH_MODE=supabase needs SUPABASE_URL (token issuer) and a JWKS URL")
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
