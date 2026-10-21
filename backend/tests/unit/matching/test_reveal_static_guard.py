"""Static reveal guard, layer 2 of 3 (plan M5 task 6; docs/design/M5-matching.md §2 "Reveal guard").

Every ``FROM`` / ``JOIN`` relation named in SQL strings under ``scout/matching/adapters/`` must be a ``match_*``
table or view or ``joint_idea``: identity tables are reachable only through ``match_reveal_v``. The test also
asserts that the adapters package holds at least one real module, so it cannot pass vacuously.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ADAPTERS = Path(__file__).resolve().parents[3] / "src" / "scout" / "matching" / "adapters"
RELATION = re.compile(r"\b(?:FROM|JOIN)\s+(?:LATERAL\s+)?([A-Za-z_][\w.]*)(\s*\()?", re.IGNORECASE)
SQL_HINT = re.compile(r"\b(select|insert|update|delete)\b", re.IGNORECASE)
ALLOWED = re.compile(r"^(match_[a-z_]+|joint_idea)$")


def _sql_strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in docstrings
        and SQL_HINT.search(n.value)
    ]


def test_adapters_package_has_real_modules() -> None:
    modules = [p for p in ADAPTERS.rglob("*.py") if p.name != "__init__.py"]
    assert modules, "scout/matching/adapters has no module yet (MatchRepo, RevealRepo, ... are missing)"


def test_every_relation_in_adapter_sql_is_a_match_relation_or_joint_idea() -> None:
    offenders: list[str] = []
    for path in sorted(ADAPTERS.rglob("*.py")):
        for sql in _sql_strings(path):
            for name, is_call in RELATION.findall(sql):
                if is_call:  # set-returning function, e.g. unnest(...)
                    continue
                if not ALLOWED.match(name.lower()):
                    offenders.append(f"{path.relative_to(ADAPTERS)}: {name}")
    assert not offenders, f"matching adapters read non-match relations: {offenders}"


def test_guard_regex_catches_a_leak() -> None:
    leak = "SELECT r.display_name FROM match_proposal mp JOIN researcher r ON r.account_id = mp.b_id"
    assert [m[0] for m in RELATION.findall(leak)] == ["match_proposal", "researcher"]
    assert not ALLOWED.match("researcher") and not ALLOWED.match("account")
