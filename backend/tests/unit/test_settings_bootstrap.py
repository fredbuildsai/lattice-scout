import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from scout.settings import Settings

ROOT = Path(__file__).resolve().parents[3]


def _s(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[arg-type]


def test_offline_defaults() -> None:
    s = _s()
    assert (s.llm_backend, s.literature_backend, s.storage_backend, s.email_provider, s.auth_mode) == (
        "fake", "fixtures", "local", "console", "dev",
    )  # fmt: skip


REAL = {
    "llm_backend": "openai_direct", "openai_api_key": "k", "literature_backend": "openalex",
    "llm_vault_sanctioned_providers": "openai",
    "email_provider": "resend", "email_api_key": "k", "email_from": "a@b.c",
    "storage_backend": "supabase", "supabase_url": "https://x.supabase.co", "supabase_service_role_key": "k",
}  # fmt: skip


def test_prod_refuses_dev_auth_even_with_real_backends() -> None:
    with pytest.raises(ValidationError, match="AUTH_MODE"):
        _s(app_env="prod", auth_mode="dev", **REAL)


@pytest.mark.parametrize(
    ("key", "value", "needle"),
    [("llm_backend", "fake", "LLM_BACKEND"), ("literature_backend", "fixtures", "LITERATURE_BACKEND"),
     ("email_provider", "console", "EMAIL_PROVIDER")],
)  # fmt: skip
def test_prod_refuses_each_offline_backend(key: str, value: str, needle: str) -> None:
    kw = {**REAL, "auth_mode": "supabase", "supabase_url": "https://x.supabase.co", key: value}
    with pytest.raises(ValidationError, match=needle):
        _s(app_env="prod", **kw)


def test_prod_accepts_real_backends() -> None:
    assert (
        _s(
            app_env="prod",
            auth_mode="supabase",
            audit_hash_key="k" * 32,
            cors_origins=["https://app.example.edu"],
            app_base_url="https://app.example.edu",
            **REAL,
        ).app_env
        == "prod"
    )


def test_every_settings_field_is_in_env_example() -> None:
    keys = {
        k.lower()
        for k in re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(), flags=re.M)
    }
    assert [f for f in Settings.model_fields if f not in keys] == []


def test_every_env_example_key_has_a_field() -> None:
    keys = re.findall(r"^([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(), flags=re.M)
    fields = set(Settings.model_fields)
    missing = [k for k in keys if not k.startswith("VITE_") and k.lower() not in fields]
    assert missing == []


def test_build_container_is_lazy_and_light() -> None:
    """Runs in a fresh interpreter: other tests may already have imported optional adapters."""
    code = (
        "import sys\n"
        "from scout.bootstrap import build_container\n"
        "from scout.settings import Settings\n"
        "c = build_container(Settings(_env_file=None, app_env='test'))\n"
        "bad = {'openai', 'agents', 'llmrouter_free', 'litellm'} & set(sys.modules)\n"
        "sys.exit(1 if bad else 0)\n"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def test_src_never_imports_tests() -> None:
    for f in (ROOT / "backend/src").rglob("*.py"):
        assert not re.search(r"^\s*(import|from) tests\b", f.read_text(), flags=re.M), f


def test_supabase_auth_needs_url_for_issuer() -> None:
    with pytest.raises(ValidationError, match="SUPABASE_URL"):
        _s(auth_mode="supabase", supabase_jwks_url="https://x/jwks")


def test_prod_refuses_local_storage() -> None:
    with pytest.raises(ValidationError, match="STORAGE_BACKEND=local"):
        _s(app_env="prod", auth_mode="supabase", **{**REAL, "storage_backend": "local"})


PROD_OK = {**REAL, "auth_mode": "supabase", "audit_hash_key": "k" * 32, "app_base_url": "https://app.example.edu",
           "cors_origins": ["https://app.example.edu"]}  # fmt: skip


@pytest.mark.parametrize(
    ("override", "needle"),
    [({"app_base_url": "http://localhost:5173"}, "APP_BASE_URL"), ({"app_base_url": "http://app.example.edu"}, "APP_BASE_URL"),
     ({"audit_hash_key": "dev-audit-hash-key-change-me"}, "AUDIT_HASH_KEY")],
)  # fmt: skip
def test_prod_refuses_dev_defaults_that_would_leak_or_mislink(override: dict[str, str], needle: str) -> None:
    assert _s(app_env="prod", **PROD_OK).app_env == "prod"
    with pytest.raises(ValidationError, match=needle):
        _s(app_env="prod", **{**PROD_OK, **override})


ROUTER = {**PROD_OK, "llm_backend": "llm_router", "mistral_api_key": "k"}


def test_prod_refuses_llm_router_without_free_provider_acknowledgement() -> None:
    kw = {**ROUTER, "llm_vault_sanctioned_providers": "mistral"}
    with pytest.raises(ValidationError, match="LLM_FREE_PROVIDERS_ACKNOWLEDGED"):
        _s(app_env="prod", **kw)
    assert _s(app_env="prod", llm_free_providers_acknowledged=True, **kw).app_env == "prod"


def test_prod_refuses_llm_router_with_empty_vault_allowlist_unless_waived() -> None:
    kw = {**ROUTER, "llm_free_providers_acknowledged": True, "llm_vault_sanctioned_providers": ""}
    with pytest.raises(ValidationError, match="LLM_VAULT_SANCTIONED_PROVIDERS"):
        _s(app_env="prod", **kw)
    assert _s(app_env="prod", llm_vault_allow_any_provider=True, **kw).app_env == "prod"


def test_dev_llm_router_needs_neither_flag() -> None:
    kw = {**ROUTER, "app_env": "dev", "llm_vault_sanctioned_providers": ""}
    assert _s(**kw).vault_sanctioned_providers == frozenset()
