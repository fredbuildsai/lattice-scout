"""Offline eval of the gap-analysis pipeline (``make eval``): FakeLLM fixtures, FixtureLiterature, in-memory services.

Runs every case in ``eval/cases`` through the real pipeline (the runner of ``scripts/make_discovery_fixtures.py``),
computes quality metrics, writes ``eval/reports/<timestamp>.json`` and prints a Markdown table. Exit code 1 when a
threshold fails: 3 <= shown <= 10 (per case) and verified-evidence rate >= 0.6. Needs no network.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).resolve().parents[1]
MIN_VERIFIED_RATE = 0.6


def _load_runner() -> ModuleType:
    sys.path.insert(0, str(ROOT / "backend"))
    spec = importlib.util.spec_from_file_location("make_discovery_fixtures", ROOT / "scripts" / "make_discovery_fixtures.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["make_discovery_fixtures"] = mod
    spec.loader.exec_module(mod)
    return mod


def _text(o) -> str:  # type: ignore[no-untyped-def]
    return " ".join([o.title, *(c.text for c in o.visible_claims())]).lower()


def _metrics(mod: ModuleType, run, themes: list[list[str]], seconds: float) -> dict[str, object]:  # type: ignore[no-untyped-def]
    opps = run.opportunities
    shown = [o for o in opps if o.status.value == "shown"]
    evidence = [e for o in opps for c in o.claims for e in c.evidence]
    reached_critic = [o for o in opps if o.critic_version]
    rejected = [o for o in opps if o.status.value == "rejected_by_critic"]
    texts = [_text(o) for o in shown]
    recalled = sum(1 for t in themes if any(all(k.lower() in x for k in t) for x in texts))
    return {
        "case": run.case.id, "status": run.status, "degraded": run.degraded, "shown": len(shown),
        "drafts": len(opps),
        "verified_evidence_rate": round(sum(1 for e in evidence if e.verified) / len(evidence), 3) if evidence else 0.0,
        "critic_rejection_rate": round(len(rejected) / len(reached_critic), 3) if reached_critic else 0.0,
        "already_done_rate": round(sum(1 for o in opps if o.status_reason == "already_done") / len(opps), 3) if opps else 0.0,
        "duplicate_rate": round(sum(1 for o in opps if o.status.value == "duplicate") / len(opps), 3) if opps else 0.0,
        "theme_recall": f"{recalled}/{len(themes)}", "cost_usd": run.cost_usd, "llm_calls": run.llm_calls,
        "openalex_calls": run.openalex_calls, "seconds": round(seconds, 2),
        "problems": mod.check_run(run),
    }  # fmt: skip


async def main() -> int:
    """Run all cases; return the process exit code."""
    mod = _load_runner()
    rows = []
    for case in mod.load_cases():
        raw = yaml.safe_load((ROOT / "eval" / "cases" / f"{case.id}.yaml").read_text(encoding="utf-8"))
        started = time.perf_counter()
        run = await mod.run_case(case)
        rows.append(_metrics(mod, run, raw.get("expected_themes", []), time.perf_counter() - started))
    failed = [r for r in rows if r["problems"] or float(r["verified_evidence_rate"]) < MIN_VERIFIED_RATE]  # type: ignore[arg-type]
    report = {"at": datetime.now(UTC).isoformat(), "min_verified_evidence_rate": MIN_VERIFIED_RATE, "cases": rows}
    out = ROOT / "eval" / "reports"
    out.mkdir(exist_ok=True)
    (out / f"{datetime.now(UTC):%Y%m%dT%H%M%S}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    cols = ["case", "status", "shown", "drafts", "verified_evidence_rate", "critic_rejection_rate", "already_done_rate", "theme_recall", "cost_usd", "seconds"]
    print("| " + " | ".join(cols) + " |\n|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join(str(r[c]) for c in cols) + " |")
    for r in failed:
        print(f"FAIL {r['case']}: {r['problems'] or 'verified evidence rate below threshold'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
