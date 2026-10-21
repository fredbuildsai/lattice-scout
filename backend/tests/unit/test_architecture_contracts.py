"""Architecture contracts are enforced and violations are detected.

Covers plan task M0.2 (acceptance: "An import-linter violation fails CI,
demonstrated by a test contract") and ARCHITECTURE section 3 / hexagonal rules
(domain -> nothing; application/ports never import adapters or frameworks;
adapters never import application).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2]
PYPROJECT = BACKEND / "pyproject.toml"


def _lint_imports(cwd: Path, config: Path | None = None) -> subprocess.CompletedProcess[str]:
    exe = Path(sys.executable).parent / "lint-imports"
    cmd = [str(exe)] if exe.exists() else [sys.executable, "-m", "importlinter"]
    if config is not None:
        cmd += ["--config", str(config)]
    env = {**os.environ, "PYTHONPATH": str(cwd)}
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def _real_config() -> dict[str, Any]:
    cfg: dict[str, Any] = tomllib.loads(PYPROJECT.read_text())["tool"]["importlinter"]
    return cfg


def _toml_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(i) for i in items) + "]"


def _write_config(path: Path, cfg: dict[str, Any]) -> None:
    lines = [
        "[tool.importlinter]",
        f"root_package = {json.dumps(cfg['root_package'])}",
        f"include_external_packages = {str(cfg['include_external_packages']).lower()}",
    ]
    for c in cfg["contracts"]:
        lines += [
            "",
            "[[tool.importlinter.contracts]]",
            f"name = {json.dumps(c['name'])}",
            f"type = {json.dumps(c['type'])}",
            f"source_modules = {_toml_list(c['source_modules'])}",
            f"forbidden_modules = {_toml_list(c['forbidden_modules'])}",
        ]
    path.write_text("\n".join(lines) + "\n")


def test_real_package_satisfies_all_contracts() -> None:
    result = _lint_imports(BACKEND)
    assert result.returncode == 0, result.stdout + result.stderr


def test_violations_are_detected_and_each_contract_reported_broken(tmp_path: Path) -> None:
    cfg = _real_config()
    # Materialise every internal module named in the contracts so they resolve.
    internal = {
        m
        for c in cfg["contracts"]
        for m in c["source_modules"] + c["forbidden_modules"]
        if m.startswith("scout")
    }
    for mod in internal | {"scout"}:
        pkg = tmp_path.joinpath(*mod.split("."))
        pkg.mkdir(parents=True, exist_ok=True)
    for d in [tmp_path, *tmp_path.rglob("*")]:
        if d.is_dir():
            (d / "__init__.py").touch()

    root = tmp_path / "scout"
    (root / "discovery/domain/bad.py").write_text("import fastapi\n")
    (root / "discovery/application/bad.py").write_text("import scout.adapters\n")
    (root / "adapters/bad.py").write_text("import scout.discovery.application\n")
    # per-context violations (review finding M0-1)
    (root / "vault/application/bad.py").write_text("import scout.vault.adapters\n")
    (root / "vault/adapters/bad.py").write_text("import scout.vault.application\n")
    (root / "vault/domain/bad.py").write_text("import scout.vault.application\n")
    (root / "matching/domain/bad.py").write_text("import scout.vault.domain\n")
    (root / "discovery/domain/leak.py").write_text("import scout.vault.domain\n")

    config = tmp_path / "pyproject.toml"
    _write_config(config, cfg)

    result = _lint_imports(tmp_path, config)
    out = result.stdout + result.stderr
    assert result.returncode != 0, out
    broken = {
        c["name"]
        for c in cfg["contracts"]
        if not c["name"].endswith("domains") and "independent" not in c["name"]
    }
    broken.add("matching domain is independent of other contexts' domains")
    for name in broken:
        assert f"{name} BROKEN" in out, out
    assert "vault domain is independent of other contexts' domains KEPT" in out, out
