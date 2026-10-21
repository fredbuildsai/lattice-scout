#!/usr/bin/env python3
"""Docs checks run by `make docs-check` and CI.

1. Internal markdown links under docs/, docs-site/ and the root README resolve to existing files.
2. Every ADR file in docs/adr/ starts with a title line and has a Status line.
Docstring coverage (interrogate) and endpoint summaries are checked separately.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "CLAUDE.md", ROOT / "PROGRESS.md"]
    for d in ("docs", "docs-site/pages"):
        files += [p for p in (ROOT / d).rglob("*.md") if "screens" not in p.parts]
    files += list((ROOT / "backend/src").rglob("README.md"))
    return [f for f in files if f.exists()]


def check_links() -> list[str]:
    errors: list[str] = []
    for f in markdown_files():
        text = re.sub(r"```.*?```|<!--.*?-->", "", f.read_text(), flags=re.S)
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = target.split("#", 1)[0]
            if path and not (f.parent / path).resolve().exists():
                errors.append(f"{f.relative_to(ROOT)}: broken link -> {target}")
    return errors


def check_adrs() -> list[str]:
    errors: list[str] = []
    for f in (ROOT / "docs/adr").glob("*.md"):
        text = f.read_text()
        if not text.startswith("# ADR") or "**Status:**" not in text:
            errors.append(f"{f.relative_to(ROOT)}: missing title or status")
    return errors


def main() -> int:
    errors = check_links() + check_adrs()
    for e in errors:
        print(e)
    print(f"docs check: {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
