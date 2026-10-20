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


@pytest.mark.parametrize("bad", [{}, {"auth_mode": "supabase", "supabase_url": "https://x.supabase.co"}])
def test_prod_refuses_offline_backends(bad: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        _s(app_env="prod", **bad)


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
