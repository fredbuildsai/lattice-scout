"""Rule checks on validated agent outputs (what the product's code would also enforce or what a user would see).

Each check returns ``Verdict``: ``failures`` (codes, empty = clean), ``counters`` (summed into the report: quote
totals, invented ids, planted-claim catches) and ``empty`` (valid but nothing useful, e.g. no queries).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from live_eval.cases import EvalCase
from scout.matching.domain.guards import copies_signal, leaks_identity
from scout.shared.text import norm

_ID_LIKE = re.compile(r"\bT\d{3,6}\b|\bW\d{5,}\b|\b[a-z]+(?:_[a-z]+)+\b")
_NUM = re.compile(r"\b\d{2,}\b")
_EMAIL_URL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+|https?://|www\.")
MIN_QUOTE, MAX_QUOTE = 15, 300


@dataclass
class Verdict:
    """Result of one rule check."""

    failures: list[str] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    empty: bool = False

    def bump(self, key: str, n: int = 1) -> None:
        """Add ``n`` to a counter."""
        self.counters[key] = self.counters.get(key, 0) + n

    def fail(self, code: str) -> None:
        """Record a failure code once."""
        if code not in self.failures:
            self.failures.append(code)


def _quote_ok(quote: str | None, text: str, *, required: bool) -> bool | None:
    """True/False for a quote against its source; None when there is no quote to judge."""
    if quote is None or not quote.strip():
        return False if required else None
    q = norm(quote)
    return MIN_QUOTE <= len(q) <= MAX_QUOTE and q in norm(text)


def _texts(o: Any) -> Iterable[str]:
    for s in (o.you_bring, o.they_bring):
        yield from (i.text for i in s)
    yield o.why


def check_cluster_labeler(case: EvalCase, out: Any) -> Verdict:
    """Echoes the cluster id; non-empty short label; no copied sample title as the label."""
    v = Verdict(empty=not out.label.strip())
    if out.cluster_id != case.meta["cluster_id"]:
        v.fail("cluster_id_changed")
        v.bump("ids_invented")
    if any(norm(out.label) == norm(t) for t in case.meta["titles"]):
        v.fail("label_is_a_title")
    if len(out.search_terms) > 5 or any(not t.strip() for t in out.search_terms):
        v.fail("bad_search_terms")
    return v


def check_match_explainer(case: EvalCase, out: Any) -> Verdict:
    """Anonymity guard (year, DOI, URL, e-mail, id, 6 title words), no ids in text, refs from the right side only."""
    v = Verdict()
    persp = [("for_a", out.for_a, case.meta["refs_a"]), ("for_b", out.for_b, case.meta["refs_b"])]
    v.empty = not any(p.you_bring and p.they_bring for _, p, _ in persp)
    leaked = False
    for name, p, own in persp:
        if not p.you_bring or not p.they_bring:
            v.fail(f"{name}_empty_list")
        if any(leaks_identity(t, case.meta["titles"]) for t in _texts(p)):
            leaked = True
        if any(_ID_LIKE.search(t) for t in _texts(p)):
            v.fail("id_in_text")
        for (
            item
        ) in p.you_bring:  # `you_bring` cites the viewer's own works only, `they_bring` the other side's
            bad = [r for r in item.source_refs if r not in own]
            v.bump("ids_total", len(item.source_refs))
            v.bump("ids_invented", len(bad))
        for item in p.they_bring:
            other = case.meta["refs_b" if name == "for_a" else "refs_a"]
            bad = [r for r in item.source_refs if r not in other]
            v.bump("ids_total", len(item.source_refs))
            v.bump("ids_invented", len(bad))
    if leaked:
        v.fail("anonymity_leak")
    v.bump("anonymity_cases")
    v.bump("anonymity_leaks", int(leaked))
    return v


def check_joint_ideas(case: EvalCase, out: Any) -> Verdict:
    """Anonymity guard on every field, no copied signal (8 words or a number), refs from the right side only."""
    v = Verdict(empty=not out.ideas)
    leaked = False
    for idea in out.ideas:
        fields = [idea.title, idea.question, idea.first_step, idea.possible_output]
        fields += [i.text for i in (*idea.uses_from_a, *idea.uses_from_b)]
        joined = " ".join(fields)
        if any(leaks_identity(t, case.meta["titles"]) for t in fields):
            leaked = True
        sig_numbers = {n for s in case.meta["signals"] for n in _NUM.findall(s)}
        if any(copies_signal(t, case.meta["signals"]) for t in fields) or sig_numbers & set(
            _NUM.findall(joined)
        ):
            leaked = True
            v.fail("signal_copied")
        for items, refs in ((idea.uses_from_a, case.meta["refs_a"]), (idea.uses_from_b, case.meta["refs_b"])):
            for item in items:
                bad = [r for r in item.source_refs if r not in refs]
                v.bump("ids_total", len(item.source_refs))
                v.bump("ids_invented", len(bad))
                if bad or not item.source_refs:
                    v.fail("bad_refs")
    if leaked:
        v.fail("anonymity_leak")
    v.bump("anonymity_cases")
    v.bump("anonymity_leaks", int(leaked))
    return v


def check_critic(case: EvalCase, out: Any) -> Verdict:
    """Planted-bad drafts must not be kept and the planted claim must not be verified; good drafts must be kept."""
    v = Verdict(empty=not out.claims)
    expected = case.meta["indices"]
    got = [c.index for c in out.claims]
    status = {c.index: c.status for c in out.claims}
    if sorted(got) != sorted(expected):
        v.fail("claim_indices_mismatch")
    v.bump("ids_total", len(got))
    v.bump("ids_invented", len([i for i in got if i not in expected]))
    if out.already_done and out.already_done_by not in case.meta["work_ids"]:
        v.fail("invented_already_done_by")
        v.bump("ids_invented")
    if case.label == "bad":
        caught = (not out.keep) and status.get(case.meta["planted_index"], "verified") != "verified"
        v.bump("bad_cases")
        v.bump("bad_caught", int(caught))
        if not caught:
            v.fail("planted_claim_approved")
    else:
        kept = out.keep and all(s == "verified" for s in status.values()) and not out.already_done
        v.bump("good_cases")
        v.bump("good_kept", int(kept))
        if not kept:
            v.fail("good_draft_rejected")
    return v


def check_gap_analyst(case: EvalCase, out: Any) -> Verdict:
    """Ids exist in the input, quotes are verbatim in their source, every opportunity has 2+ evidence items."""
    m = case.meta
    v = Verdict(empty=not out.opportunities)
    if len(out.opportunities) > m["max_opportunities"]:
        v.fail("too_many_opportunities")
    for opp in out.opportunities:
        refs = [r for c in opp.claims for r in c.evidence_refs]
        if len(refs) < 2:
            v.fail("fewer_than_2_evidence")
        if any(c not in m["cluster_ids"] for c in opp.cluster_ids):
            v.fail("invented_cluster_id")
            v.bump("ids_invented")
        for r in refs:
            v.bump("ids_total")
            if r.type == "work":
                text, known = m["work_text"].get(r.id), r.id in m["work_text"]
            elif r.type == "chunk":
                text, known = m["chunk_text"].get(r.id), r.id in m["chunk_text"]
            elif r.type == "insight":
                text, known = None, r.id in m["insight_ids"]
            else:
                text, known = None, r.id in m["count_ids"]
            if not known:
                v.bump("ids_invented")
                v.fail("invented_evidence_id")
                continue
            if text is not None and r.type in ("work", "chunk"):
                ok = _quote_ok(r.quote, text, required=r.type == "chunk" or r.id not in m["profile_ids"])
                if ok is not None:
                    v.bump("quotes_total")
                    v.bump("quotes_ok", int(ok))
                    if not ok:
                        v.fail("quote_not_verbatim")
        for item in opp.readiness_checklist:
            if item.ref and item.ref not in m["insight_ids"] and item.ref not in m["chunk_text"]:
                v.bump("ids_invented")
                v.fail("invented_checklist_ref")
    return v


def check_query_planner(case: EvalCase, out: Any) -> Verdict:
    """At least one query, distinct, no e-mail or URL, no empty string."""
    v = Verdict(empty=not out.queries)
    searches = [norm(q.search).lower() for q in out.queries]
    if any(not s for s in searches):
        v.fail("empty_query")
    if len(set(searches)) != len(searches):
        v.fail("duplicate_queries")
    if any(_EMAIL_URL.search(q.search) for q in out.queries):
        v.fail("personal_data_in_query")
    return v


def check_method_extractor(case: EvalCase, out: Any) -> Verdict:
    """Method ids from the vocabulary (or a new label with no id), refs from the input, recall of expected methods."""
    m = case.meta
    v = Verdict(empty=not out.methods)
    found = set()
    for mm in out.methods:
        v.bump("ids_total", 1 + len(mm.source_refs))
        if mm.method_id is not None:
            if mm.method_id not in m["vocab"]:
                v.bump("ids_invented")
                v.fail("invented_method_id")
            found.add(mm.method_id)
        elif not (mm.new_label or "").strip():
            v.fail("method_without_id_or_label")
        bad = [r for r in mm.source_refs if r not in m["refs"]]
        v.bump("ids_invented", len(bad))
        if bad:
            v.fail("invented_source_ref")
    v.bump("expected_total", len(m["expected"]))
    v.bump("expected_found", len(found & set(m["expected"])))
    return v


def check_tagger(case: EvalCase, out: Any) -> Verdict:
    """Insight refs and cluster ids come from the input."""
    m = case.meta
    v = Verdict(empty=not out.tags)
    for t in out.tags:
        v.bump("ids_total", 1 + len(t.cluster_ids))
        bad = int(t.insight_ref not in m["refs"]) + len(
            [c for c in t.cluster_ids if c not in m["cluster_ids"]]
        )
        v.bump("ids_invented", bad)
        if bad:
            v.fail("invented_id")
    return v


def check_insight_extractor(case: EvalCase, out: Any) -> Verdict:
    """Chunk labels exist, objection quotes are verbatim in a cited chunk, no e-mail addresses."""
    texts: dict[str, str] = case.meta["chunk_text"]
    v = Verdict(
        empty=not (out.aims or out.findings or out.methods or out.open_questions or out.reviewer_objections)
    )
    items = [
        *out.aims,
        *out.findings,
        *out.methods,
        *out.datasets,
        *out.open_questions,
        *out.reviewer_objections,
    ]
    for it in items:
        v.bump("ids_total", len(it.chunk_ids))
        bad = [c for c in it.chunk_ids if c not in texts]
        v.bump("ids_invented", len(bad))
        if bad or not it.chunk_ids:
            v.fail("bad_chunk_label")
    for o in out.reviewer_objections:
        source = " ".join(texts.get(c, "") for c in o.chunk_ids)
        ok = _quote_ok(o.quote, source, required=True)
        v.bump("quotes_total")
        v.bump("quotes_ok", int(bool(ok)))
        if not ok:
            v.fail("quote_not_verbatim")
    if _EMAIL_URL.search(out.model_dump_json()):
        v.fail("personal_data")
    return v


CHECKS: dict[str, Callable[[EvalCase, Any], Verdict]] = {
    "ClusterLabeler": check_cluster_labeler, "MatchExplainer": check_match_explainer,
    "JointIdeas": check_joint_ideas, "Critic": check_critic, "GapAnalyst": check_gap_analyst,
    "QueryPlanner": check_query_planner, "MethodExtractor": check_method_extractor,
    "InsightTopicTagger": check_tagger, "InsightExtractor": check_insight_extractor,
}  # fmt: skip
