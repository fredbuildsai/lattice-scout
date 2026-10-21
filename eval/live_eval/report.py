"""Records, metrics, thresholds and the go / no-go verdict of a live eval run."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

SCHEMA_REJECT_ERRORS = (
    "BadRequest",
    "UnsupportedParams",
    "UnprocessableEntity",
    "InvalidRequest",
    "ResponseFormat",
)
GO, GO_PUBLISHED, NO_GO, INCOMPLETE = "GO", "GO_PUBLISHED_ONLY", "NO_GO", "INCOMPLETE"


@dataclass
class CallRecord:
    """One executed case (one agent call, plus its schema-mode fallback if any)."""

    deployment: str
    agent: str
    case_id: str
    run: int
    data_class: str
    status: str  # ok | invalid | unavailable
    latency_s: float
    provider_calls: int  # provider requests used (the quantity --max-calls caps)
    retries: int
    rate_limited: int
    quota_events: int
    schema_mode: str  # schema | json_mode
    schema_rejected: bool = False
    fallback_used: bool = False
    empty: bool = False
    failures: list[str] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """Plain dict (no model output or input text is ever stored)."""
        return asdict(self)


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (``q`` in 0..100); None for no data."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(q / 100 * len(s)) - 1)]


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def agent_metrics(records: list[CallRecord]) -> dict[str, Any]:
    """Metrics of one agent on one deployment."""
    n = len(records)
    ok = [r for r in records if r.status == "ok"]
    invalid = [r for r in records if r.status == "invalid"]
    unavailable = [r for r in records if r.status == "unavailable"]
    valid = len(ok) + len(invalid)
    clean = [r for r in ok if not r.failures]
    c: dict[str, int] = defaultdict(int)
    for r in ok:
        for k, v in r.counters.items():
            c[k] += v
    first_try = [r for r in records if r.schema_mode == "schema" or r.fallback_used]
    lat = [r.latency_s for r in records]
    m: dict[str, Any] = {
        "calls": n, "ok": len(ok), "invalid": len(invalid), "unavailable": len(unavailable),
        "schema_valid_rate": _rate(len(ok), valid),
        "unavailable_rate": _rate(len(unavailable), n),
        "empty_rate": _rate(sum(1 for r in ok if r.empty), len(ok)),
        "rule_pass_rate": _rate(len(clean), len(ok)),
        "latency_p50_s": _round(percentile(lat, 50)), "latency_p95_s": _round(percentile(lat, 95)),
        "retries": sum(r.retries for r in records), "rate_limit_events": sum(r.rate_limited for r in records),
        "quota_events": sum(r.quota_events for r in records), "provider_calls": sum(r.provider_calls for r in records),
        "strict_schema_acceptance": _rate(len(first_try) - sum(r.schema_rejected for r in first_try), len(first_try)),
        "schema_rejections": sum(r.schema_rejected for r in records),
        "fallback_ok_rate": _rate(sum(1 for r in records if r.fallback_used and r.status == "ok"), sum(1 for r in records if r.fallback_used)),
        "quote_verbatim_rate": _rate(c["quotes_ok"], c["quotes_total"]),
        "invented_id_count": c["ids_invented"] if (c["ids_total"] or ok) else None,
        "anonymity_leak_rate": _rate(c["anonymity_leaks"], c["anonymity_cases"]),
        "bad_catch_rate": _rate(c["bad_caught"], c["bad_cases"]),
        "good_keep_rate": _rate(c["good_kept"], c["good_cases"]),
        "expected_recall": _rate(c["expected_found"], c["expected_total"]),
    }  # fmt: skip
    failures: dict[str, int] = defaultdict(int)
    for r in ok:
        for f in r.failures:
            failures[f] += 1
    m["failure_codes"] = dict(sorted(failures.items(), key=lambda kv: -kv[1]))
    return m


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


def load_thresholds(path: Path) -> dict[str, Any]:
    """Read ``eval/thresholds.yaml``."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def evaluate(agent: str, metrics: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, Any]:
    """Compare metrics with ``default`` thresholds overridden by the agent's own; ``min_x`` / ``max_x`` keys.

    A metric that was not measured (None) fails its threshold, except where it cannot apply (``not_applicable``)
    because the agent has no such metric at all: only thresholds the agent's own section names are mandatory
    beyond the defaults.
    """
    rules = {**thresholds.get("default", {}), **thresholds.get("agents", {}).get(agent, {})}
    checks = []
    for key, limit in rules.items():
        kind, _, metric = key.partition("_")
        value = metrics.get(metric)
        own = key in thresholds.get("agents", {}).get(agent, {})
        if value is None and not own:
            continue  # a default threshold for a metric this agent does not produce
        ok = value is not None and (value >= limit if kind == "min" else value <= limit)
        checks.append({"metric": metric, "rule": key, "limit": limit, "value": value, "ok": bool(ok)})
    enough = metrics["calls"] - metrics["unavailable"] >= int(thresholds.get("min_calls_per_agent", 2))
    status = "insufficient_data" if not enough else ("pass" if all(c["ok"] for c in checks) else "fail")
    return {"status": status, "checks": checks}


def verdict(per_agent: dict[str, dict[str, Any]], thresholds: dict[str, Any]) -> dict[str, Any]:
    """Go / no-go for one deployment.

    * NO_GO: a required agent that was evaluated fails.
    * GO: every required agent (published and confidential) was evaluated and passes.
    * GO_PUBLISHED_ONLY: the required published agents pass, a required confidential agent was not evaluated
      (not run, or too few completed calls). Matches the pilot on published data only (DECISIONS 129/130).
    * INCOMPLETE: a required published agent has insufficient data.
    """
    req_pub = list(thresholds.get("required_published", []))
    req_conf = list(thresholds.get("required_confidential", []))

    def st(a: str) -> str:
        return str(per_agent.get(a, {}).get("evaluation", {}).get("status", "not_run"))

    failed = [a for a in (*req_pub, *req_conf) if st(a) == "fail"]
    reasons = [
        f"{a}: failed " + ", ".join(c["rule"] for c in per_agent[a]["evaluation"]["checks"] if not c["ok"])
        for a in failed
    ]
    if failed:
        return {"verdict": NO_GO, "reasons": reasons}
    pub_missing = [a for a in req_pub if st(a) != "pass"]
    if pub_missing:
        return {"verdict": INCOMPLETE, "reasons": [f"{a}: {st(a)}" for a in pub_missing]}
    conf_missing = [a for a in req_conf if st(a) != "pass"]
    if conf_missing:
        return {"verdict": GO_PUBLISHED, "reasons": [f"{a}: {st(a)}" for a in conf_missing]}
    return {"verdict": GO, "reasons": []}


def build_report(
    records: list[CallRecord], thresholds: dict[str, Any], meta: dict[str, Any]
) -> dict[str, Any]:
    """Aggregate records into per-deployment, per-agent metrics, evaluations and verdicts."""
    by_dep: dict[str, dict[str, list[CallRecord]]] = defaultdict(lambda: defaultdict(list))
    for r in records:
        by_dep[r.deployment][r.agent].append(r)
    deployments: dict[str, Any] = {}
    for dep, agents in by_dep.items():
        per_agent: dict[str, dict[str, Any]] = {}
        for agent, rs in agents.items():
            m = agent_metrics(rs)
            per_agent[agent] = {"metrics": m, "evaluation": evaluate(agent, m, thresholds)}
        deployments[dep] = {"agents": per_agent, **verdict(per_agent, thresholds)}
    return {**meta, "deployments": deployments, "records": [r.to_json() for r in records]}


def render_markdown(report: dict[str, Any]) -> str:
    """Human summary: one verdict line per deployment, then a metrics table per deployment."""
    lines = [
        "# Live eval report",
        "",
        f"Generated {report.get('at')}. Free models only. Prompt versions: {report.get('prompt_versions')}.",
        "",
    ]
    lines += ["## Go / no-go per deployment (pilot)", "", "| Deployment | Verdict | Why |", "|---|---|---|"]
    for dep, d in report["deployments"].items():
        lines.append(
            f"| {dep} | **{d['verdict']}** | {'; '.join(d['reasons']) or 'all required agents pass'} |"
        )
    lines += [
        "",
        "GO: all required agents pass. GO_PUBLISHED_ONLY: published agents pass, confidential-class agents not evaluated or too few calls (pilot on published data only). NO_GO: a required agent fails. INCOMPLETE: not enough data.",
        "",
    ]
    cols = [
        "calls",
        "schema_valid_rate",
        "empty_rate",
        "rule_pass_rate",
        "unavailable_rate",
        "latency_p50_s",
        "latency_p95_s",
        "retries",
        "rate_limit_events",
        "strict_schema_acceptance",
    ]
    for dep, d in report["deployments"].items():
        lines += [
            f"## {dep}",
            "",
            "| Agent | Status | " + " | ".join(cols) + " | Failing rules |",
            "|" + "---|" * (len(cols) + 3),
        ]
        for agent, a in d["agents"].items():
            m, ev = a["metrics"], a["evaluation"]
            bad = (
                ", ".join(
                    f"{c['rule']}={c['value']} (limit {c['limit']})" for c in ev["checks"] if not c["ok"]
                )
                or "-"
            )
            lines.append(
                f"| {agent} | {ev['status']} | " + " | ".join(_fmt(m[c]) for c in cols) + f" | {bad} |"
            )
        extra = []
        for agent, a in d["agents"].items():
            m = a["metrics"]
            for k in (
                "bad_catch_rate",
                "good_keep_rate",
                "quote_verbatim_rate",
                "anonymity_leak_rate",
                "invented_id_count",
                "expected_recall",
            ):
                if m.get(k) is not None:
                    extra.append(f"{agent}.{k}={m[k]}")
            if m["failure_codes"]:
                extra.append(f"{agent} failures: {m['failure_codes']}")
        lines += ["", "Rule metrics: " + ("; ".join(extra) or "-"), ""]
    return "\n".join(lines) + "\n"


def _fmt(x: Any) -> str:
    return "-" if x is None else str(x)
