"""Run eval cases against one or more backends and collect ``CallRecord``s.

Safety rules enforced here (not only by flags):
* a confidential-class agent runs only on a ``synthetic`` case, and only on a backend the operator listed in
  ``--allow-synthetic-confidential``; otherwise the case is skipped, never sent;
* a published-class agent's input must hold no confidential part;
* the number of provider requests per backend is capped (``max_calls``), counting router retries.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from live_eval.cases import EvalCase
from live_eval.checks import CHECKS
from live_eval.report import SCHEMA_REJECT_ERRORS, CallRecord
from scout.adapters.llm.factory import AgentFactory
from scout.discovery.domain.agent_io import (
    ClusterLabel,
    CriticVerdict,
    GapAnalysis,
    InsightTopicTags,
    QueryPlan,
)
from scout.matching.domain.agent_io import JointIdeaList, MatchRationale, MethodSet
from scout.ports.llm import CallContext, ConfidentialDataRefused, LLMOutputInvalid, LLMPort, LLMUnavailable
from scout.shared.confidential import contains_confidential
from scout.vault.domain.insights import DocumentInsights

OUTPUT_TYPES: dict[str, type[Any]] = {
    "ClusterLabeler": ClusterLabel, "MatchExplainer": MatchRationale, "GapAnalyst": GapAnalysis, "Critic": CriticVerdict,
    "QueryPlanner": QueryPlan, "MethodExtractor": MethodSet, "InsightExtractor": DocumentInsights,
    "InsightTopicTagger": InsightTopicTags, "JointIdeas": JointIdeaList,
}  # fmt: skip
QUOTA_HINTS = ("quota", "insufficientquota", "daily")


@dataclass
class Attempts:
    """Per-call provider attempts, filled by the usage meter (or a scripted backend)."""

    rows: list[tuple[str, str | None]] = field(default_factory=list)  # (status, error_type)

    def note(self, status: str, error_type: str | None = None) -> None:
        """Remember one provider request."""
        self.rows.append((status, error_type))

    def record_llm_call(self, rec: Any) -> None:
        """``UsageMeterPort``: the real adapter reports one row per provider request."""
        self.note(str(getattr(rec.status, "value", rec.status)), rec.error_type)

    def record_external_call(self, rec: Any) -> None:
        """``UsageMeterPort``: not used by the eval."""

    def drain(self) -> list[tuple[str, str | None]]:
        """Return and clear the rows."""
        rows, self.rows = self.rows, []
        return rows


@dataclass
class Backend:
    """One deployment under test."""

    name: str
    llm: LLMPort
    attempts: Attempts
    allow_synthetic_confidential: bool = False
    json_mode: bool = False  # set after the provider rejected the schema response format
    used_calls: int = 0


def _is_schema_rejection(rows: Iterable[tuple[str, str | None]]) -> bool:
    return any(e and any(h.lower() in e.lower() for h in SCHEMA_REJECT_ERRORS) for _, e in rows)


def _count(rows: list[tuple[str, str | None]]) -> tuple[int, int]:
    limited = sum(1 for s, _ in rows if s == "rate_limited")
    quota = sum(1 for s, e in rows if s == "rate_limited" and e and any(h in e.lower() for h in QUOTA_HINTS))
    return limited, quota


def _guard(case: EvalCase) -> None:
    """Hard stop: nothing confidential-class is ever sent unless the case is synthetic sample text."""
    if case.data_class == "confidential" and not case.synthetic:
        raise ConfidentialDataRefused(f"{case.id}: confidential-class case is not synthetic")
    if (
        case.data_class == "published"
        and not isinstance(case.input, dict)
        and contains_confidential(case.input)
    ):
        raise ConfidentialDataRefused(f"{case.id}: published-class input holds a confidential part")


async def _one(
    backend: Backend, factory: AgentFactory, case: EvalCase, run: int, remaining: int
) -> list[CallRecord]:
    spec = factory.spec(case.agent, OUTPUT_TYPES[case.agent])
    ctx = CallContext(feature=f"live_eval.{case.agent}", run_id=uuid4())
    out: list[CallRecord] = []
    rejected = False
    fallback = False
    for attempt in range(2):
        use = dataclasses.replace(spec, strict_schema=False) if backend.json_mode else spec
        mode = "json_mode" if backend.json_mode else "schema"
        backend.attempts.drain()
        started = time.perf_counter()
        status, output = "ok", None
        try:
            result = await factory.run(use, case.input, ctx)
            output = result.output
        except LLMOutputInvalid:
            status = "invalid"
        except LLMUnavailable:
            status = "unavailable"
        latency = time.perf_counter() - started
        rows = backend.attempts.drain()
        used = max(len(rows), 1)
        backend.used_calls += used
        if (
            status == "unavailable"
            and not backend.json_mode
            and _is_schema_rejection(rows)
            and attempt == 0
            and remaining - used > 0
        ):
            backend.json_mode, rejected, fallback = True, True, True
            out.append(
                CallRecord(
                    backend.name,
                    case.agent,
                    case.id,
                    run,
                    case.data_class,
                    "unavailable",
                    latency,
                    used,
                    0,
                    *_count(rows),
                    mode,
                    schema_rejected=True,
                )
            )
            continue
        limited, quota = _count(rows)
        rec = CallRecord(
            backend.name, case.agent, case.id, run, case.data_class, status, latency, used,
            max(0, used - 1), limited, quota, mode, schema_rejected=rejected, fallback_used=fallback,
        )  # fmt: skip
        if output is not None:
            try:
                verdict = CHECKS[case.agent](case, output)
                rec.failures, rec.counters, rec.empty = verdict.failures, verdict.counters, verdict.empty
            except Exception as exc:  # a bug in a check must show up, not hide the model result
                rec.failures = [f"check_error:{type(exc).__name__}"]
        out.append(rec)
        break
    # the rejected first attempt is folded into the final record so each case counts once
    if len(out) == 2:
        first, last = out
        last.provider_calls += first.provider_calls
        last.rate_limited += first.rate_limited
        last.quota_events += first.quota_events
        last.latency_s += first.latency_s
        last.schema_rejected, last.fallback_used = True, True
        return [last]
    return out


async def run_backend(
    backend: Backend,
    factory: AgentFactory,
    cases: list[EvalCase],
    *,
    runs: int,
    max_calls: int,
    pause_s: float = 0.0,
) -> tuple[list[CallRecord], list[str]]:
    """Run ``cases`` x ``runs`` on one backend until done or ``max_calls`` provider requests were used."""
    records: list[CallRecord] = []
    notes: list[str] = []
    skipped = 0
    for run in range(runs):
        for case in cases:
            if case.data_class == "confidential" and not backend.allow_synthetic_confidential:
                skipped += 1
                continue
            _guard(case)
            if backend.used_calls >= max_calls:
                notes.append(
                    f"{backend.name}: stopped at the call cap ({max_calls}); {len(cases) * runs - len(records) - skipped} cases not run"
                )
                return records, notes
            records.extend(await _one(backend, factory, case, run, max_calls - backend.used_calls))
            if pause_s:
                await asyncio.sleep(pause_s)
    if skipped:
        notes.append(
            f"{backend.name}: {skipped} confidential-class case runs skipped (not in --allow-synthetic-confidential)"
        )
    return records, notes


def make_factory(llm: LLMPort, prompts_dir: Path, field_name: str = "materials science") -> AgentFactory:
    """The product's agent factory (same prompts, same input filtering)."""
    return AgentFactory(llm, prompts_dir, {"field_name": field_name})
