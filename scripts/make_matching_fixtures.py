#!/usr/bin/env python3
"""Write the ``FakeLLM`` fixtures of the three matching agents for the Foster/Moreira demo pair.

The script runs the **real** matching application in memory: profile claim and import from the recorded OpenAlex
fixtures (``ProfileService`` + ``ImportProfile`` + ``FixtureLiterature``), then ``ComputeResearcherProfile`` for both
researchers, ``ComputeMatches`` in reconnect mode (both exclusion filters off, DECISIONS 83), two accepts on the
proposal and ``RevealMatch``. Agents run through ``AgentsMatching`` and the real ``AgentFactory`` (so the prompt
front-matter is checked), on ``FakeLLM`` scripted with the authoring functions below. Each authoring function sees
exactly the input the code sends; the script records ``(input, output)`` and writes
``backend/tests/fixtures/llm/<Agent>/<input_hash>.json`` plus the canonical input as ``<input_hash>.input.json``.

Output: ``MethodExtractor`` x2 (Foster, Moreira) plus one edge case (a synthetic sample source with no method, so
``{"methods": []}``), ``MatchExplainer`` x1 and ``JointIdeas`` x1 for the pair. No ``default.json`` is written: a
pair without a fixture (the synthetic dev accounts) must fall back to the template.

Authoring is deterministic and grounded: a method is reported only when a cue below matches the source text, with
the refs of the matching sources; explanation and idea items cite only works of the side that brings them, matched by
the same cues; every MatchExplainer text is checked against the anonymity guard (``leaks_identity``) over all titles
of both researchers, and every idea against ``check_ideas``.

Assumptions the seed must keep (or the fixtures miss and the template is used):
* Foster's account id sorts before Moreira's (Foster is side ``a``); here ``UUID(int=0xF0)`` and ``UUID(int=0xF1)``.
* Matching consent on; no vault documents for either account (so no signals, whatever ``consent_vault_signals`` is).
* The clock is 2026-10-21 (works window, recency weights and ISO week depend on it).
* The reconnect run turns the two exclusion filters off only. The pair scores about 0.119 with formula ``c2``, above
  the default ``MATCH_MIN_SCORE`` 0.11 (it scored 0.055 under ``c1``, which needed a lowered threshold; F-39).

Title fallback: the recorded OpenAlex works of both researchers carry no abstract, so with ``method_works`` keeping
only works with an abstract the profile job never calls MethodExtractor and the pair can never be proposed. Until the
application uses the title when a work has no abstract (``text = (w.abstract or w.title)[:1500]``), this script
applies that rule in a ``ProfileSource`` wrapper and prints a NOTE; once the application does it, the wrapper is
skipped automatically and the hashes stay the same.

Re-run after any change to the OpenAlex fixtures, ``config/methods.yaml``, ``matching/domain/agent_io.py`` input
models or the application code that builds agent inputs (``profile_job.py``, ``compute.py``, ``reveal_job.py``).

Usage: ``cd backend && uv run python ../scripts/make_matching_fixtures.py [--check]``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import struct
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self, cast
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))  # ``tests.fakes``: the in-memory repositories of the test suite

from pydantic import BaseModel  # noqa: E402
from tests.fakes import (  # noqa: E402
    FakeLLM,
    FixedClock,
    FixtureLiterature,
    InMemoryJobQueue,
    InMemoryUsageMeter,
    SequentialIdGenerator,
)
from tests.fakes.profile import InMemoryProfileUoW, ProfileStore  # noqa: E402

from scout.adapters.llm.factory import AgentFactory  # noqa: E402
from scout.adapters.llm.fake_llm import canonical_json, input_hash  # noqa: E402
from scout.adapters.observability.prices import load_price_table  # noqa: E402
from scout.matching.adapters.agents import AgentsMatching  # noqa: E402
from scout.matching.adapters.vocabulary import load_vocabulary  # noqa: E402
from scout.matching.application.compute import ComputeMatches  # noqa: E402
from scout.matching.application.config import MatchConfig  # noqa: E402
from scout.matching.application.profile_job import ComputeResearcherProfile  # noqa: E402
from scout.matching.application.reveal_job import RevealMatch, check_ideas  # noqa: E402
from scout.matching.application.sources import ProfileSourceAdapter  # noqa: E402
from scout.matching.domain.agent_io import (  # noqa: E402
    JointIdea,
    JointIdeaList,
    JointIdeasInput,
    MatchExplainerInput,
    MatchRationale,
    MethodExtractorInput,
    MethodMention,
    MethodSet,
    MethodSource,
    Perspective,
    ResearcherSummary,
    SignalItem,
    VocabularyEntry,
)
from scout.matching.domain.enums import Action, MatchState  # noqa: E402
from scout.matching.domain.guards import leaks_identity  # noqa: E402
from scout.matching.domain.profile import MatchProfile, WorkIn, method_works  # noqa: E402
from scout.matching.domain.proposal import BringItem, MatchProposal  # noqa: E402
from scout.matching.ports.repos import MatchUnitOfWork  # noqa: E402
from scout.matching.ports.sources import Consent, ProfileInput  # noqa: E402
from scout.ports.llm import AgentResult, AgentSpec, CallContext  # noqa: E402
from scout.profile.application.import_profile import ImportProfile  # noqa: E402
from scout.profile.application.service import ProfileService  # noqa: E402
from scout.settings import Settings  # noqa: E402
from scout.shared import AccountId  # noqa: E402

PROMPTS = ROOT / "prompts"
OUT = ROOT / "backend" / "tests" / "fixtures" / "llm"
OPENALEX = ROOT / "tests" / "fixtures" / "openalex"
VOCABULARY = ROOT / "config" / "methods.yaml"
AGENTS = ("MethodExtractor", "MatchExplainer", "JointIdeas")
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
FOSTER = AccountId(UUID(int=0xF0))
MOREIRA = AccountId(UUID(int=0xF1))
PEOPLE: dict[AccountId, tuple[str, list[str]]] = {
    FOSTER: ("foster", ["A5059588273", "A5140167166"]),  # split record with the same ORCID, claimed together
    MOREIRA: ("moreira", ["A5033675494"]),
}
# Reconnect mode for the demo pair (co-authors at the same institution): only the exclusion filters are off. Under
# formula c2 the pair scores about 0.119, above the default MATCH_MIN_SCORE 0.11, so no lowered threshold is needed.
# The threshold is not part of any agent input, so it does not change the fixture hashes.
RECONNECT = MatchConfig(exclude_coauthors=False, exclude_same_group=False)
_HASH_FILE = re.compile(r"^[0-9a-f]{16}(\.input)?\.json$")

# ==== cues ========================================================================================================
# key (vocabulary method id, or a proposed new label) -> (case-insensitive regex, confidence). A source supports the
# key when one cue matches its text; the mention's confidence is the best one matched.
Cue = tuple[str, str]
METHOD_CUES: dict[str, tuple[Cue, ...]] = {
    "lca": ((r"carbon footprint|life[- ]cycle", "medium"),),
    "raman": ((r"\braman\b", "high"),),
    "foaming": ((r"\bfoam(s|ing)?\b", "high"),),
    "nanocellulose_isolation": (
        (r"nanocrystals isolated|isolation of (cellulose )?nano", "high"),
        (r"nanocellulose|cellulose nanofib", "medium"),
    ),
    "lignin_fractionation": ((r"lignin nanoparticles|lignin fractionation", "medium"),),
    "chemical_modification": ((r"sulfonated|grafted|esterif", "high"),),
    "polymer_synthesis": ((r"pmma spheres: synthesis|polymer synthesis", "high"), (r"polyimide|pd\(ii\)-polymer", "medium")),
    "membrane_fabrication": ((r"(ion-selective|exchange|adsorption) membranes?", "high"),),
    "adsorption_testing": ((r"\badsorption\b", "high"),),
    "photocatalysis": ((r"photocataly", "high"),),
    "electrochemistry": ((r"electrochem", "high"),),
    "machine_learning": ((r"machine learning|self-driving laborator", "high"),),
}  # fmt: skip
NEW_LABEL_CUES: dict[str, tuple[Cue, ...]] = {
    "EEM fluorescence spectroscopy": ((r"\beem fluorescence\b", "high"),),
    "droplet templating": ((r"droplet-templat", "medium"),),
    "heterogeneous catalysis testing": ((r"(?<!photo)cataly[st]|cross-coupling|guerbet|\bco oxidation\b", "medium"),),
}  # fmt: skip
CUES = {**METHOD_CUES, **NEW_LABEL_CUES}


def _match(key: str, text: str) -> str | None:
    """Best confidence of the cues of ``key`` found in ``text``."""
    found = [conf for rx, conf in CUES[key] if re.search(rx, text, re.IGNORECASE)]
    return "high" if "high" in found else ("medium" if found else None)


def _refs(key: str, works: Sequence[Any]) -> list[str]:
    """Refs of the works whose title supports ``key`` (at most 3)."""
    return [w.ref for w in works if _match(key, w.title)][:3]


# ==== authoring: MethodExtractor ==================================================================================
def extract_methods(data: MethodExtractorInput) -> MethodSet:
    """One mention per cue key found in the sources, refs copied from the input; vocabulary order first."""
    vocab = [v.method_id for v in data.vocabulary]
    unknown = set(METHOD_CUES) - set(vocab)
    if unknown:
        raise AssertionError(f"cue ids not in the vocabulary: {sorted(unknown)}")
    mentions: list[MethodMention] = []
    for key in [*(k for k in vocab if k in METHOD_CUES), *NEW_LABEL_CUES]:
        hits = [(s.source_ref, c) for s in data.sources if (c := _match(key, s.text))]
        if not hits:
            continue
        conf = "high" if any(c == "high" for _, c in hits) else "medium"
        refs = [r for r, _ in hits][:20]
        if key in METHOD_CUES:
            mentions.append(MethodMention(method_id=key, source_refs=refs, confidence=conf))
        else:
            mentions.append(MethodMention(new_label=key, source_refs=refs, confidence=conf))
    return MethodSet(methods=mentions[:25])


EDGE_SOURCE = MethodSource(
    source_ref="work:W0000001",
    text=(
        "Sample abstract (synthetic, not attributed to anyone). This perspective discusses the market outlook for "
        "bio-based packaging and the policy questions it raises for regional waste systems."
    ),
)


# ==== authoring: MatchExplainer ===================================================================================
# Short, non-identifying phrases per method (checked against the anonymity guard below); the label otherwise. The
# order is the preference when a side has more than three distinct methods (the input carries no method weights).
BRING: dict[str, str] = {
    "photocatalysis": "Photocatalytic films that break down pollutants in water",
    "nanocellulose_isolation": "Isolating nanocellulose with tuned surface chemistry",
    "foaming": "Foam processing with lignin-based polyols",
    "raman": "Raman spectroscopy of fibre and lignin interfaces",
    "polymer_synthesis": "Synthesis of polymer and hybrid catalyst materials",
    "chemical_modification": "Chemical modification of cellulose and chitosan",
    "lca": "Carbon-footprint assessment of bio-based composites",
    "lignin_fractionation": "Fractionating lignin into nanoparticles",
    "membrane_fabrication": "Fabrication of ion-selective and adsorption membranes",
    "electrochemistry": "Electrochemical treatment of persistent pollutants",
    "adsorption_testing": "Adsorption testing of water contaminants",
    "machine_learning": "Machine learning for faster materials discovery",
}
_PREFERENCE = {k: i for i, k in enumerate(BRING)}


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s[1:2].islower() else s


def _names(data: MatchExplainerInput | JointIdeasInput) -> tuple[dict[str, str], dict[str, str]]:
    """Topic id/name -> name and method id/label -> id, from both summaries."""
    topics = {k: t.name for t in (*data.a.topics, *data.b.topics) for k in (t.id, t.name)}
    methods = {k: m.id for m in (*data.a.methods, *data.b.methods) for k in (m.id, m.label)}
    return topics, methods


def _ranked(method_ids: Sequence[str]) -> list[str]:
    return sorted(method_ids, key=lambda i: (_PREFERENCE.get(i, len(_PREFERENCE)), i))[:3]


def _items(summary: ResearcherSummary, method_ids: Sequence[str]) -> list[BringItem]:
    labels = {m.id: m.label for m in summary.methods}
    return [
        BringItem(
            text=BRING.get(i) or labels.get(i, i), source_refs=_refs(i, summary.works) if i in CUES else []
        )
        for i in method_ids
    ]


def explain(data: MatchExplainerInput) -> MatchRationale:
    """Mirrored cards from the distinct methods and the two strongest shared topics."""
    topics, methods = _names(data)
    weight = {t.id: t.weight for t in data.a.topics}
    for t in data.b.topics:
        weight[t.id] = weight.get(t.id, 0.0) + t.weight
    shared = sorted(data.score_breakdown.shared_topics, key=lambda x: (-weight.get(x, 0.0), x))
    shared_names = [_lower_first(topics.get(x, x)) for x in shared[:2]]
    a_ids = _ranked(
        [methods.get(x, x) for x in data.score_breakdown.a_only_methods] or [m.id for m in data.a.methods]
    )
    b_ids = _ranked(
        [methods.get(x, x) for x in data.score_breakdown.b_only_methods] or [m.id for m in data.b.methods]
    )
    a_items, b_items = _items(data.a, a_ids), _items(data.b, b_ids)
    topic_part = " and ".join(shared_names) if shared_names else "related topics"
    labels = {m.id: m.label for m in (*data.a.methods, *data.b.methods)}
    a_first, b_first = (_lower_first(labels.get(x[0], x[0])) for x in (a_ids, b_ids))

    def why(mine: str, theirs: str) -> str:
        return (
            f"You both work on {topic_part}. Combining your {mine} with their {theirs} could open questions "
            "that neither of you covers alone."
        )

    return MatchRationale(
        for_a=Perspective(you_bring=a_items, they_bring=b_items, why=why(a_first, b_first)),
        for_b=Perspective(you_bring=b_items, they_bring=a_items, why=why(b_first, a_first)),
    )


# ==== authoring: JointIdeas =======================================================================================
@dataclass(frozen=True)
class IdeaT:
    """An idea template; an item is kept when its cue key matches a work of that side."""

    title: str
    question: str
    from_a: tuple[tuple[str, str], ...]  # (cue key, text)
    from_b: tuple[tuple[str, str], ...]
    first_step: str
    possible_output: str


IDEAS: list[IdeaT] = [
    IdeaT("Photocatalysts on nanocellulose supports",
          "Can isolated nanocellulose act as a support that keeps photocatalysts active under visible light and easy "
          "to recover after water treatment?",
          (("nanocellulose_isolation", "Nanocellulose isolation with surface esterification"),),
          (("photocatalysis", "Photocatalytic films and hybrids active under LED and solar light"),),
          "Coat a nanocellulose film with one existing photocatalyst and compare activity and recovery with the bare "
          "powder; worth checking the literature first.",
          "Pilot dataset and a short joint paper"),
    IdeaT("Tracking photocatalyst surfaces with deep-UV Raman",
          "Can deep-UV Raman and fluorescence mapping show how hybrid photocatalyst surfaces change during repeated "
          "use?",
          (("raman", "Deep-UV Raman and fluorescence mapping of interfaces"),),
          (("photocatalysis", "Hybrid photocatalysts tested under low-power light"),),
          "Measure fresh and used samples of one hybrid photocatalyst on the existing Raman set-up.",
          "Method note and a shared dataset"),
    IdeaT("Lignin foams as catalyst carriers",
          "Can flexible lignin-based polyurethane foams carry palladium catalysts for coupling reactions in green "
          "solvents and be reused?",
          (("foaming", "Formulation of flexible lignin polyurethane foams"),),
          (("heterogeneous catalysis testing", "Palladium and copper catalysis in sustainable solvents"),),
          "Load one palladium precatalyst into two foam formulations and run a benchmark coupling reaction.",
          "Feasibility data for a joint proposal"),
]  # fmt: skip


def joint_ideas(data: JointIdeasInput) -> JointIdeaList:
    """The templates whose items are grounded in works of both sides (signals are never cited or copied)."""

    def side(items: tuple[tuple[str, str], ...], summary: ResearcherSummary) -> list[BringItem]:
        return [BringItem(text=t, source_refs=r) for k, t in items if (r := _refs(k, summary.works))]

    ideas = []
    for t in IDEAS:
        ua, ub = side(t.from_a, data.a), side(t.from_b, data.b)
        if ua and ub:
            ideas.append(
                JointIdea(
                    title=t.title,
                    question=t.question,
                    uses_from_a=ua,
                    uses_from_b=ub,
                    first_step=t.first_step,
                    possible_output=t.possible_output,
                )  # fmt: skip
            )
    if not ideas:
        raise AssertionError("no idea template is grounded on both sides")
    return JointIdeaList(ideas=ideas[:3])


AUTHORS: dict[str, Callable[[Any], BaseModel]] = {
    "MethodExtractor": extract_methods,
    "MatchExplainer": explain,
    "JointIdeas": joint_ideas,
}


# ==== PostgreSQL parity ===========================================================================================
# match_profile.activity and the topic/method weights are ``real`` (float4) columns; the score sent to MatchExplainer
# is computed from the values read back, so the in-memory store rounds them the same way (see make_discovery_fixtures).
def pg_real(x: float) -> float:
    """The value a PostgreSQL ``real`` column gives back for ``x``."""
    packed = struct.pack("<f", x)
    f32 = struct.unpack("<f", packed)[0]
    for digits in range(1, 10):
        candidate = float(f"{f32:.{digits}g}")
        if struct.pack("<f", candidate) == packed:
            return candidate
    return float(f32)


def as_stored(p: MatchProfile) -> MatchProfile:
    """The profile as the SQL repository loads it: float4 values; topics and methods by weight desc, then id."""
    topics = sorted((replace(t, weight=pg_real(t.weight)) for t in p.topics), key=lambda t: (-t.weight, t.id))
    methods = sorted(((k, pg_real(v)) for k, v in p.methods.items()), key=lambda kv: (-kv[1], kv[0]))
    return replace(p, activity=pg_real(p.activity), topics=tuple(topics), methods=dict(methods))


# ==== in-memory matching store (only what the three jobs use) =====================================================
@dataclass
class MatchStore:
    """Shared state of the unit of work below."""

    profiles: dict[AccountId, MatchProfile] = field(default_factory=dict)
    label_proposals: dict[AccountId, list[tuple[str, tuple[str, ...]]]] = field(default_factory=dict)
    matches: dict[UUID, MatchProposal] = field(default_factory=dict)
    ideas: dict[UUID, list[JointIdea]] = field(default_factory=dict)


class _Profiles:
    def __init__(self, k: MatchStore) -> None:
        self._k = k

    async def get(self, account: AccountId) -> MatchProfile | None:
        return self._k.profiles.get(account)

    async def get_many(self, accounts: Sequence[AccountId]) -> dict[AccountId, MatchProfile]:
        return {a: self._k.profiles[a] for a in accounts if a in self._k.profiles}

    async def save(self, profile: MatchProfile, proposals: Sequence[tuple[str, tuple[str, ...]]]) -> None:
        self._k.profiles[AccountId(profile.account_id)] = as_stored(profile)
        self._k.label_proposals[AccountId(profile.account_id)] = list(proposals)

    async def delete(self, account: AccountId) -> None:
        self._k.profiles.pop(account, None)

    async def accounts(self) -> list[AccountId]:
        return sorted(self._k.profiles)


_ACTIVE = {MatchState.PROPOSED, MatchState.A_ACCEPTED, MatchState.B_ACCEPTED, MatchState.MUTUAL}


class _Candidates:
    def __init__(self, k: MatchStore) -> None:
        self._k = k

    async def candidates(
        self, *, field: str, account: AccountId | None, **_: Any
    ) -> list[tuple[AccountId, AccountId]]:
        """Eligible pairs with a shared topic and no active proposal (the filters are off in reconnect mode)."""
        pool = sorted(a for a, p in self._k.profiles.items() if p.eligible and p.field == field)
        busy = {(m.a_id, m.b_id) for m in self._k.matches.values() if m.state in _ACTIVE}
        out = []
        for i, a in enumerate(pool):
            for b in pool[i + 1 :]:
                ta = {t.id for t in self._k.profiles[a].topics}
                if ta & {t.id for t in self._k.profiles[b].topics} and (a, b) not in busy:
                    if account is None or account in (a, b):
                        out.append((a, b))
        return out


class _Matches:
    def __init__(self, k: MatchStore) -> None:
        self._k = k

    async def add(self, proposal: MatchProposal) -> bool:
        pair = (proposal.a_id, proposal.b_id)
        if proposal.id in self._k.matches or any(
            (m.a_id, m.b_id) == pair and m.state in _ACTIVE for m in self._k.matches.values()
        ):
            return False
        self._k.matches[proposal.id] = proposal
        return True

    async def get_any(self, match_id: UUID, lock: bool = False) -> MatchProposal | None:
        return self._k.matches.get(match_id)

    async def save(self, proposal: MatchProposal) -> None:
        self._k.matches[proposal.id] = proposal

    async def open_counts(self, accounts: Sequence[AccountId], since: datetime) -> dict[AccountId, int]:
        out = dict.fromkeys(accounts, 0)
        for m in self._k.matches.values():
            if m.created_at >= since:
                for x in (m.a_id, m.b_id):
                    if x in out:
                        out[x] += 1
        return out


class _Ideas:
    def __init__(self, k: MatchStore) -> None:
        self._k = k

    async def add_many(
        self, match_id: UUID, ideas: Sequence[JointIdea], prompt_version: str, model: str, now: datetime
    ) -> None:
        self._k.ideas.setdefault(match_id, list(ideas))

    async def list(self, match_id: UUID) -> list[JointIdea]:
        return list(self._k.ideas.get(match_id, []))


class MiniMatchUoW:
    """Unit of work over ``MatchStore``; writes apply at once (the jobs commit every write they make)."""

    def __init__(self, k: MatchStore) -> None:
        self.profiles, self.candidates = _Profiles(k), _Candidates(k)
        self.matches, self.ideas = _Matches(k), _Ideas(k)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    async def commit(self) -> None:
        """No-op."""

    async def rollback(self) -> None:
        """No-op."""


# ==== sources and notifier ========================================================================================
def title_fallback_wired() -> bool:
    """True once the application uses the title of a work without abstract (then no wrapper is needed)."""
    return bool(method_works([WorkIn(ref="work:W1", title="t", abstract=None, year=2020, cited_by=0)]))


class TitleFallbackSource:
    """``ProfileSource`` that sets ``abstract = title`` for works without an abstract (the rule the app should own)."""

    def __init__(self, inner: ProfileSourceAdapter) -> None:
        self._inner = inner

    async def matching_input(self, account: AccountId) -> ProfileInput | None:
        """The inner input with titles standing in for missing abstracts."""
        data = await self._inner.matching_input(account)
        if data is None:
            return None
        works = tuple(w if w.abstract else replace(w, abstract=w.title or None) for w in data.works)
        return replace(data, works=works)


class Consents:
    """Matching consent on for both demo accounts."""

    async def get(self, account: AccountId) -> Consent | None:
        """Consent of a demo account."""
        return (
            Consent(matching=True, vault_signals=False, field=RECONNECT.field) if account in PEOPLE else None
        )


class NoSignals:
    """No vault documents for the demo pair."""

    async def signals(self, account: AccountId, sections: tuple[str, ...], limit: int) -> list[SignalItem]:
        """Nothing."""
        return []


class SilentNotifier:
    """``MatchNotifier`` that does nothing."""

    async def proposed(self, account_id: AccountId, match_id: UUID) -> None:
        """Ignore."""

    async def mutual(self, account_id: AccountId, match_id: UUID) -> None:
        """Ignore."""


class Recording:
    """``LLMPort`` around ``FakeLLM`` that records every matching agent call (input, validated output)."""

    def __init__(self, inner: FakeLLM) -> None:
        self.inner = inner
        self.calls: list[tuple[str, BaseModel, BaseModel]] = []

    def confidential_allowed(self, tier: Any = None) -> bool:
        """The agent factory asks the port before every call (ADR 016); the fake allows everything."""
        return self.inner.confidential_allowed(tier)

    async def run(self, agent: AgentSpec[Any], input: Any, ctx: CallContext) -> AgentResult[Any]:
        """Delegate; keep the pair."""
        result = await self.inner.run(agent, input, ctx)
        if agent.name in AGENTS:
            self.calls.append((agent.name, input, result.output))
        return result


# ==== run =========================================================================================================
@dataclass
class PairRun:
    """What the run produced."""

    calls: list[tuple[str, BaseModel, BaseModel]]
    profiles: dict[AccountId, MatchProfile]
    proposal: MatchProposal | None
    reveal: dict[str, Any]
    ideas: list[JointIdea]
    titles: list[str]
    shimmed: bool


async def run_pair(extra: dict[AccountId, tuple[str, list[str]]] | None = None) -> PairRun:
    """Claim, import, profile, propose (reconnect mode), accept twice and reveal, in memory (fixed clock).

    ``extra`` adds more recorded researchers (used by ``calibrate_matching.py`` for the third test subject); the
    fixtures are only ever written from the default two.
    """
    people = {**PEOPLE, **(extra or {})}
    s = Settings(_env_file=None, app_env="test")
    clock, ids = FixedClock(NOW), SequentialIdGenerator(1)
    fake = FakeLLM(OUT, InMemoryUsageMeter(), load_price_table(ROOT / s.llm_price_table), clock, ids)
    for name, fn in AUTHORS.items():
        fake.script(name, fn)
    llm = Recording(fake)
    agents = AgentsMatching(AgentFactory(llm, PROMPTS, {"field_name": s.launch_field}))
    lit = FixtureLiterature(OPENALEX)
    jobs = InMemoryJobQueue(clock, ids)
    pstore, mstore = ProfileStore(), MatchStore()

    def puow() -> InMemoryProfileUoW:
        return InMemoryProfileUoW(pstore)

    def muow() -> MatchUnitOfWork:
        return cast(
            "MatchUnitOfWork", MiniMatchUoW(mstore)
        )  # structural subset: the repos the three jobs use

    profile = ProfileService(puow, lit, jobs, clock, ids)
    importer = ImportProfile(puow, lit, clock, ids)
    for account, (_, authors) in people.items():
        await profile.claim(account, authors, authors[0], None)
        while (job := await jobs.claim("fixtures", ["profile.import"])) is not None:
            await jobs.complete(job.id, dict(await importer.handle(job.payload, last_attempt=False)))
    shimmed = not title_fallback_wired()
    base = ProfileSourceAdapter(profile)
    source = TitleFallbackSource(base) if shimmed else base
    vocab = load_vocabulary(VOCABULARY)
    consents, signals = Consents(), NoSignals()
    profile_job = ComputeResearcherProfile(
        muow, source, consents, signals, agents, vocab, jobs, clock, RECONNECT
    )
    for account in people:
        await profile_job.execute(account)
    await agents.extract_methods(  # edge case: a synthetic source without any method
        MethodExtractorInput(
            vocabulary=[VocabularyEntry(method_id=t.id, label=t.label, synonyms=list(t.synonyms)) for t in vocab.terms],
            sources=[EDGE_SOURCE],
        ),
        CallContext(feature="matching.profile", account_id=None, session_id=None, run_id=None),
    )  # fmt: skip
    await ComputeMatches(muow, agents, vocab, SilentNotifier(), clock, RECONNECT).execute(None)
    proposal = next(iter(mstore.matches.values()), None)
    reveal: dict[str, Any] = {}
    if proposal is not None:
        for who in (FOSTER, MOREIRA):
            proposal.decide(who, Action.ACCEPT, NOW)
        mstore.matches[proposal.id] = proposal
        reveal = await RevealMatch(muow, source, consents, signals, agents, vocab, clock).handle(
            {"match_id": str(proposal.id)}, last_attempt=True
        )
    titles = []
    for account in PEOPLE:
        data = await base.matching_input(account)
        titles += [w.title for w in data.works] if data else []
    return PairRun(
        llm.calls, dict(mstore.profiles), proposal, reveal, mstore.ideas.get(proposal.id, []) if proposal else [],
        titles, shimmed,
    )  # fmt: skip


def check_run(r: PairRun) -> list[str]:
    """The demo pair is proposed with an LLM rationale, the texts pass the guards and the reveal gives ideas."""
    problems = []
    names = [n for n, _, _ in r.calls]
    for agent, want in (("MethodExtractor", 3), ("MatchExplainer", 1), ("JointIdeas", 1)):
        if names.count(agent) != want:
            problems.append(f"{agent}: {names.count(agent)} calls, expected {want}")
    for account, (who, _) in PEOPLE.items():
        p = r.profiles.get(account)
        if p is None or not p.eligible:
            problems.append(f"{who}: profile missing or not eligible")
    if r.proposal is None:
        return [*problems, "no proposal for the pair (score below MATCH_MIN_SCORE or no distinct method)"]
    if (r.proposal.a_id, r.proposal.b_id) != (FOSTER, MOREIRA):
        problems.append("Foster is not side a")
    if r.proposal.rationale.source != "llm":
        problems.append(f"rationale source {r.proposal.rationale.source}")
    for name, inp, out in r.calls:
        if (
            name == "MatchExplainer"
            and isinstance(out, MatchRationale)
            and isinstance(inp, MatchExplainerInput)
        ):
            refs_a, refs_b = {w.ref for w in inp.a.works}, {w.ref for w in inp.b.works}
            for label, persp, mine, theirs in (
                ("for_a", out.for_a, refs_a, refs_b),
                ("for_b", out.for_b, refs_b, refs_a),
            ):
                texts = [persp.why, *(i.text for i in (*persp.you_bring, *persp.they_bring))]
                if any(leaks_identity(t, r.titles) for t in texts):
                    problems.append(f"MatchExplainer {label}: a text fails the anonymity guard")
                if not (
                    {x for i in persp.you_bring for x in i.source_refs} <= mine
                    and {x for i in persp.they_bring for x in i.source_refs} <= theirs
                ):
                    problems.append(f"MatchExplainer {label}: a ref is not from the right side")  # fmt: skip
        if name == "JointIdeas" and isinstance(out, JointIdeaList) and isinstance(inp, JointIdeasInput):
            ra = {w.ref for w in inp.a.works} | {x.ref for x in inp.a_signals}
            rb = {w.ref for w in inp.b.works} | {x.ref for x in inp.b_signals}
            sig = [x.text for x in (*inp.a_signals, *inp.b_signals)]
            if len(check_ideas(list(out.ideas), ra, rb, sig)) != len(out.ideas):
                problems.append("JointIdeas: an idea fails check_ideas")
    if r.reveal.get("status") != "ready" or not r.ideas:
        problems.append(f"reveal {r.reveal}")
    return problems


# ==== files =======================================================================================================
def _dump(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"


def wanted_files(r: PairRun) -> dict[Path, str]:
    """Every fixture file of the run (outputs and canonical inputs)."""
    files: dict[Path, str] = {}
    for name, inp, out in r.calls:
        h = input_hash(inp)
        body = _dump(out)
        path = OUT / name / f"{h}.json"
        if path in files and files[path] != body:
            raise AssertionError(f"{path.name}: two different outputs for one input")
        files[path] = body
        files[OUT / name / f"{h}.input.json"] = (
            json.dumps(json.loads(canonical_json(inp)), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        )
    return files


def summary(r: PairRun) -> str:
    """One line for the run."""
    methods = {PEOPLE[a][0]: sorted(p.methods) for a, p in r.profiles.items()}
    score = f"{r.proposal.score:.3f}" if r.proposal else "-"
    return (
        f"methods={methods} score={score} rationale={r.proposal.rationale.source if r.proposal else '-'} "
        f"ideas={len(r.ideas)} calls={len(r.calls)}"
    )


def main() -> int:
    """Generate (or with ``--check`` compare) the fixtures; remove stale hash-named files."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--check", action="store_true", help="exit 1 if a fixture is missing, differs or is stale"
    )
    args = ap.parse_args()
    r = asyncio.run(run_pair())
    print(summary(r))
    if r.shimmed:
        print("NOTE: the application does not yet use titles for works without abstract; fixtures assume "
              "text = (abstract or title)[:1500] (see the module docstring)")  # fmt: skip
    bad = 0
    problems = check_run(r)
    for p in problems:
        print(f"  PROBLEM {p}")
        bad += 1
    files = wanted_files(r)
    for path, body in sorted(files.items()):
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != body:
                print(f"differs: {path.relative_to(ROOT)}")
                bad += 1
        elif not problems:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
    for name in AGENTS:
        for stale in sorted((OUT / name).glob("*.json")):
            if _HASH_FILE.match(stale.name) and stale not in files:
                print(f"stale: {stale.relative_to(ROOT)}")
                bad += 1
                if not args.check and not problems:
                    stale.unlink()
    print(f"{sum(1 for p in files if not p.name.endswith('.input.json'))} fixture outputs")
    return 1 if bad and (args.check or problems) else 0


if __name__ == "__main__":
    sys.exit(main())
