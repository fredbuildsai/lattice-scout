"""``eval/run_eval.py --live``: argument parsing, orchestration, report files and the go / no-go summary."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from live_eval.cases import ALL_AGENTS, EvalCase, build_cases
from live_eval.report import GO, GO_PUBLISHED, build_report, load_thresholds, render_markdown
from live_eval.runner import Attempts, Backend, make_factory, run_backend
from live_eval.scripted import BEHAVIORS, ScriptedLLM

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAX_CALLS = 60
MAX_CALLS_CEILING = 500


def _split(values: list[str] | None) -> list[str]:
    return [x.strip() for v in values or [] for x in v.split(",") if x.strip()]


def parser() -> argparse.ArgumentParser:
    """Arguments of the live mode."""
    p = argparse.ArgumentParser(
        prog="run_eval.py --live", description="Score real free models on Scout's agents."
    )
    p.add_argument("--live", action="store_true", help="live mode (required)")
    p.add_argument(
        "--deployment",
        action="append",
        help="deployment name(s) from config/llm_router.yaml, comma separated",
    )
    p.add_argument("--route", help="route name: every deployment of that route is evaluated, one by one")
    p.add_argument("--agents", help=f"comma list; default all of {', '.join(ALL_AGENTS)}")
    p.add_argument("--runs", type=int, default=1, help="repeat every case N times (default 1)")
    p.add_argument(
        "--max-calls",
        type=int,
        default=DEFAULT_MAX_CALLS,
        help=f"cap on provider requests PER deployment, retries included (default {DEFAULT_MAX_CALLS}, max {MAX_CALLS_CEILING})",
    )
    p.add_argument(
        "--allow-synthetic-confidential",
        action="append",
        metavar="DEPLOYMENT",
        help="deployment(s) that may receive the SYNTHETIC sample inputs of confidential-class agents (default: none, those agents are skipped)",
    )
    p.add_argument(
        "--out",
        help="report path, .json or .md or a bare prefix; both files are written (default eval/reports/live-<date>)",
    )
    p.add_argument("--thresholds", default=str(ROOT / "eval" / "thresholds.yaml"))
    p.add_argument("--config", default=str(ROOT / "config" / "llm_router.yaml"))
    p.add_argument(
        "--dry-run",
        nargs="?",
        const="perfect",
        choices=BEHAVIORS,
        help="no network: scripted fake model (default behavior perfect)",
    )
    return p


def _out_paths(out: str | None) -> tuple[Path, Path]:
    base = Path(out) if out else ROOT / "eval" / "reports" / f"live-{datetime.now(UTC):%Y%m%d}"
    if not base.is_absolute():
        base = Path.cwd() / base
    if base.suffix in (".json", ".md"):
        base = base.with_suffix("")
    base.parent.mkdir(parents=True, exist_ok=True)
    return base.with_suffix(".json"), base.with_suffix(".md")


async def _collect(args: argparse.Namespace, cases: list[EvalCase]) -> tuple[list[Any], list[str]]:
    from live_eval import backends

    notes: list[str] = []
    jobs: list[tuple[Backend, float]] = []
    if args.dry_run:
        at = Attempts()
        jobs.append(
            (Backend(f"scripted-{args.dry_run}", ScriptedLLM(cases, at, args.dry_run), at, True), 0.0)
        )
    else:
        config = backends.load_config(Path(args.config))
        names = backends.select_deployments(config, _split(args.deployment), args.route)
        allowed = set(_split(args.allow_synthetic_confidential))
        for stray in sorted(allowed - set(names)):
            notes.append(f"--allow-synthetic-confidential names {stray!r}, which is not being evaluated")
        for name in names:
            backend, pause, why = backends.build_backend(
                config, name, allow_synthetic_confidential=name in allowed
            )
            if backend is None:
                notes.append(f"{name}: skipped ({why})")
            else:
                jobs.append((backend, pause))
    records: list[Any] = []
    for backend, pause in jobs:
        factory = make_factory(backend.llm, ROOT / "prompts")
        recs, n = await run_backend(
            backend, factory, cases, runs=args.runs, max_calls=args.max_calls, pause_s=pause
        )
        records += recs
        notes += n
    return records, notes


async def live_main(argv: list[str]) -> int:
    """Run the live eval; exit codes: 0 go (or published-only go), 1 no-go / incomplete, 2 usage or nothing ran."""
    args = parser().parse_args(argv)
    if not (args.deployment or args.route or args.dry_run):
        print("Pass --deployment NAME[,NAME] or --route NAME (see config/llm_router.yaml), or --dry-run.")
        return 2
    if not 1 <= args.max_calls <= MAX_CALLS_CEILING or args.runs < 1:
        print(f"--max-calls must be 1..{MAX_CALLS_CEILING} and --runs >= 1.")
        return 2
    agents = _split([args.agents]) if args.agents else None
    if agents and (bad := [a for a in agents if a not in ALL_AGENTS]):
        print(f"Unknown agent(s) {bad}.")
        return 2
    cases = build_cases(agents)
    try:
        records, notes = await _collect(args, cases)
    except ValueError as exc:
        print(str(exc))
        return 2
    for n in notes:
        print(f"note: {n}")
    if not records:
        print("Nothing ran (no usable deployment: check the keys and --deployment). No report written.")
        return 2
    thresholds = load_thresholds(Path(args.thresholds))
    versions = {c.agent: None for c in cases}
    meta = {
        "at": datetime.now(UTC).isoformat(), "mode": "dry-run" if args.dry_run else "live", "runs": args.runs,
        "max_calls_per_deployment": args.max_calls, "notes": notes, "prompt_versions": _versions(versions),
    }  # fmt: skip
    report = build_report(records, thresholds, meta)
    jpath, mpath = _out_paths(args.out)
    jpath.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    mpath.write_text(render_markdown(report), encoding="utf-8")
    print(render_markdown(report))
    print(f"Wrote {jpath} and {mpath}")
    verdicts = [d["verdict"] for d in report["deployments"].values()]
    return 0 if all(v in (GO, GO_PUBLISHED) for v in verdicts) else 1


def _versions(agents: dict[str, None]) -> dict[str, str]:
    from live_eval.runner import OUTPUT_TYPES
    from scout.adapters.llm.prompts import load_agent_spec

    return {a: load_agent_spec(ROOT / "prompts", a, OUTPUT_TYPES[a]).prompt_version for a in agents}
