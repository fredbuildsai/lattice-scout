#!/usr/bin/env python3
"""Write the ``FakeLLM`` fixtures of the five discovery agents for the eval cases (``eval/cases/*.yaml``).

For each case (Johan Foster with the three fictional sample documents; Raphaell Moreira profile-only) the script
runs the **real** application in memory: profile claim and import from the recorded OpenAlex fixtures, upload and
ingestion of the sample PDFs (real extractor, chunker and the ``InsightExtractor`` fixtures), then the whole
``discovery.run`` job (``StartAnalysisRun`` + ``RunAnalysis`` + ``AgentsDiscovery``) with ``FixtureLiterature``. Each
discovery agent is answered by an authoring function below that sees exactly the input the code sends; the script
records ``(input, output)`` and writes ``backend/tests/fixtures/llm/<Agent>/<input_hash>.json`` plus the canonical
input as ``<input_hash>.input.json`` (for review and diffing).

Authoring is deterministic and grounded: the ClusterLabeler's ``search_terms`` reproduce the recorded count queries
``n1``-``n4`` exactly; every GapAnalyst quote is asserted to be verbatim in the abstract or chunk it cites (one
deliberate exception, flagged ``broken=True``, shows the verifier hiding a bad citation); counts and numbers in
claims are copied from the measured ``novelty`` rows. The script then checks the run itself (shown count within the
case bounds, every shown opportunity passes the show rule with literature and count evidence).

The clock is fixed at 2026-10-21 (``year_from`` 2020, novelty since 2021): fixture keys depend on the year, so tests
and the eval runner must use the same date. Re-run after any change to the cases, sample documents, OpenAlex
fixtures, InsightExtractor fixtures, ``agent_io.py`` input models or the pipeline code that builds agent inputs.

Usage: ``cd backend && uv run python ../scripts/make_discovery_fixtures.py [--check]``
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import re
import struct
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Self
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))  # ``tests.fakes``: the in-memory repositories of the test suite

import yaml  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from tests.fakes import (  # noqa: E402
    FakeLLM,
    FixedClock,
    FixtureLiterature,
    InMemoryFileStorage,
    InMemoryJobQueue,
    InMemoryUsageMeter,
    InMemoryVaultUoW,
    SequentialIdGenerator,
    VaultStore,
)
from tests.fakes.discovery import (  # noqa: E402
    DiscoveryStore,
    InMemoryClusterRepo,
    InMemoryDiscoveryUoW,
    InMemoryLedger,
)
from tests.fakes.profile import InMemoryProfileUoW, ProfileStore  # noqa: E402

from scout.adapters.llm.factory import AgentFactory  # noqa: E402
from scout.adapters.llm.fake_llm import canonical_json, input_hash  # noqa: E402
from scout.adapters.observability.prices import load_price_table  # noqa: E402
from scout.discovery.adapters.agents import AgentsDiscovery  # noqa: E402
from scout.discovery.adapters.fields import load_field  # noqa: E402
from scout.discovery.application.pipeline import PipelineConfig  # noqa: E402
from scout.discovery.application.run_analysis import RunAnalysis  # noqa: E402
from scout.discovery.application.sources import ProfileSourceAdapter, VaultSourceAdapter  # noqa: E402
from scout.discovery.application.start import StartAnalysisRun, StartConfig  # noqa: E402
from scout.discovery.domain.agent_io import (  # noqa: E402
    ClaimVerdict,
    ClusterLabel,
    ClusterLabelInput,
    CriticInput,
    CriticVerdict,
    GapAnalysis,
    GapContext,
    InsightTag,
    InsightTopicTaggerInput,
    InsightTopicTags,
    PlannedQuery,
    QueryPlan,
    QueryPlannerInput,
)
from scout.discovery.domain.corpus import Cluster  # noqa: E402
from scout.discovery.domain.enums import ClaimStatus, OppStatus, SourceType  # noqa: E402
from scout.discovery.domain.opportunity import Opportunity  # noqa: E402
from scout.discovery.ports.sources import RunProfile  # noqa: E402
from scout.ports.llm import AgentResult, AgentSpec, CallContext  # noqa: E402
from scout.profile.application.import_profile import ImportProfile  # noqa: E402
from scout.profile.application.service import ProfileService  # noqa: E402
from scout.settings import Settings  # noqa: E402
from scout.shared import AccountId  # noqa: E402
from scout.shared.text import norm  # noqa: E402
from scout.vault.adapters.extraction import PdfDocxExtractor  # noqa: E402
from scout.vault.adapters.insight_extractor import AgentsInsightExtractor  # noqa: E402
from scout.vault.application.extract_insights import ExtractInsights  # noqa: E402
from scout.vault.application.ingest import IngestDocument  # noqa: E402
from scout.vault.application.service import VaultService  # noqa: E402
from scout.vault.application.upload import UploadDocument  # noqa: E402
from scout.vault.domain.enums import RetentionPolicy  # noqa: E402

PROMPTS = ROOT / "prompts"
CASES = ROOT / "eval" / "cases"
OUT = ROOT / "backend" / "tests" / "fixtures" / "llm"
OPENALEX = ROOT / "tests" / "fixtures" / "openalex"
AGENTS = ("QueryPlanner", "ClusterLabeler", "InsightTopicTagger", "GapAnalyst", "Critic")
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
OWNER = AccountId(UUID(int=0xF0))
ALLOWED_DEGRADED = {"no_adjacent_clusters"}  # the Moreira profile-only run falls back to the largest clusters
_HASH_FILE = re.compile(r"^[0-9a-f]{16}(\.input)?\.json$")


# ==== cases =======================================================================================================
@dataclass(frozen=True)
class Case:
    """One eval case (``eval/cases/<id>.yaml``)."""

    id: str
    author_ids: list[str]
    documents: list[tuple[str, str]]  # (repo-relative markdown path, keep | discard)
    min_shown: int
    max_shown: int


def load_cases() -> list[Case]:
    """Every eval case, sorted by file name."""
    out = []
    for path in sorted(CASES.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        docs = [(str(d["path"]), str(d["retention"])) for d in raw.get("documents") or []]
        out.append(
            Case(raw["id"], list(raw["author_ids"]), docs, int(raw["min_shown"]), int(raw["max_shown"]))
        )
    return out


# ==== authoring: QueryPlanner =====================================================================================
# The six recorded searches (tests/fixtures/openalex/manifest.json q1-q6), with the input item that motivates each:
# the first `because` candidate present in the input wins (insight text prefix, topic id or keyword).
PLAN: list[tuple[str, str, list[str]]] = [
    ("lignin polyurethane foam", "core", ["insight:Formulate flexible polyurethane foams", "topic:T11208"]),
    ("cellulose nanofiber membrane flow battery", "core",
     ["insight:Evaluate the long-term cycling stability", "topic:T10610", "topic:T10409"]),
    ("PFAS adsorption membrane", "core", ["insight:Regenerate fungal-derived adsorption membranes", "topic:T11869"]),
    ("photocatalytic PFAS degradation", "bridge", ["insight:Degrade captured PFAS directly", "topic:T10078"]),
    ("lignin fiber cement", "adjacent", ["topic:T10687", "topic:T10033", "keyword:lignin"]),
    ("wood electromagnetic interference shielding", "adjacent", ["topic:T10484", "keyword:wood"]),
]  # fmt: skip


def plan_queries(data: QueryPlannerInput) -> QueryPlan:
    """The recorded queries; ``because`` points at an input item."""
    topics = {t.id for t in data.topics}
    keywords = {k.lower() for k in [*data.keywords, *data.field.keywords]}

    def because(options: list[str]) -> str:
        for opt in options:
            kind, _, key = opt.partition(":")
            if kind == "insight":
                hit = next((i.insight_ref for i in data.insights if i.text.startswith(key)), None)
                if hit:
                    return f"insight:{hit}"
            elif (kind == "topic" and key in topics) or (kind == "keyword" and key in keywords):
                return opt
        return f"field:{data.field.name}"[:80]

    return QueryPlan(
        queries=[PlannedQuery(search=s, purpose=p, because=because(b), max_results=50) for s, p, b in PLAN]
    )


# ==== authoring: ClusterLabeler ===================================================================================
# Keyed by the cluster's most frequent OpenAlex topic. `search_terms` joined = the recorded count queries n1-n4.
LABELS: dict[str, tuple[str, str, list[str]]] = {
    "T11869": ("PFAS adsorption and photocatalytic degradation",
               "Removal of PFAS from water by adsorbents and membranes, and its photocatalytic destruction.",
               ["photocatalytic regeneration", "PFAS membrane"]),  # n3
    "T11208": ("Lignin polyurethane foams",
               "Flexible polyurethane foams made with lignin polyols, including recyclable and antimicrobial foams.",
               ["lignin polyurethane foam", "flame retardant"]),  # n1
    "T10610": ("Cellulose nanofibre membranes",
               "Cellulose nanofibre production and its use as membrane reinforcement and separator material.",
               ["cellulose separator", "zinc iodine battery"]),  # n2
    "T10409": ("Nanocellulose membranes for batteries",
               "Sulfonated nanocellulose and nanofibre-derived materials for battery and fuel-cell membranes.",
               ["cellulose separator", "zinc iodine battery"]),  # n2
    "T10033": ("Lignin in fibre-cement composites",
               "Lignin and natural fibres in cement and gypsum composites, and their fibre-matrix interfaces.",
               ["lignin fluorescence", "fiber cement interface"]),  # n4
    "T11224": ("Wood composites for EMI shielding",
               "Conductive and magnetic wood-based composites that block or absorb electromagnetic interference.",
               ["wood composite", "EMI shielding"]),  # no recorded count: falls back by token overlap
}  # fmt: skip


def label_cluster(data: ClusterLabelInput) -> ClusterLabel:
    """A hand-written label for the known clusters; the topic name otherwise."""
    first = data.openalex_topics[0] if data.openalex_topics else None
    if first is not None and first.id in LABELS:
        label, description, terms = LABELS[first.id]
    else:
        name = first.name if first else "Other papers"
        label, description, terms = name[:60], f"Papers on {name.lower()}."[:200], [name[:30]]
    return ClusterLabel(cluster_id=data.cluster_id, label=label, description=description, search_terms=terms)


# ==== authoring: InsightTopicTagger ===============================================================================
# label -> (core cues, partial cues, reason); cues are case-insensitive substrings of the insight text.
TAG_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "Lignin polyurethane foams": (("foam",), ("lignin",), "Lignin-based polyurethane foam formulation or testing."),
    "Cellulose nanofibre membranes": (("cnf", "zinc", "flow cell", "flow-cell"),
                                      ("crossover", "swelling", "sulfonation", "impedance", "cycling"),
                                      "Sulfonated cellulose nanofibre membrane work."),
    "Nanocellulose membranes for batteries": (("cnf", "zinc", "flow cell", "flow-cell"),
                                              ("crossover", "swelling", "sulfonation", "cycling"),
                                              "Nanocellulose membrane for a flow cell."),
    "PFAS adsorption and photocatalytic degradation": (("pfas", "pfoa"), ("photocatal", "regenerat"),
                                                       "PFAS capture, regeneration or photocatalytic degradation."),
}  # fmt: skip


def tag_insights(data: InsightTopicTaggerInput) -> InsightTopicTags:
    """Rule-based tags on the labelled clusters (no tag when nothing fits)."""
    tags: list[InsightTag] = []
    for ins in data.insights:
        text = ins.text.lower()
        for strength in ("core", "partial"):
            hits, reason = [], ""
            for cl in data.clusters:
                rule = TAG_RULES.get(cl.label)
                if rule is None:
                    continue
                core, part, why = rule
                cues = core if strength == "core" else part
                stronger = strength == "partial" and any(c in text for c in core)
                if not stronger and any(c in text for c in cues):
                    hits.append(cl.cluster_id)
                    reason = why
            if hits:
                tags.append(
                    InsightTag(
                        insight_ref=ins.insight_ref, cluster_ids=hits[:3], strength=strength, reason=reason
                    )
                )
    return InsightTopicTags(tags=tags)


# ==== authoring: GapAnalyst =======================================================================================
@dataclass(frozen=True)
class Ref:
    """A citation template. ``work``: a corpus paper (``key`` = work id, verbatim abstract ``quote``); ``own``: one of
    the researcher's recent works (quote = its title); ``insight``: ``key`` = prefix of the insight text; ``chunk``:
    ``key`` = a verbatim passage located in the chunks; ``count``: ``key`` = cluster label (its plain probe)."""

    type: str
    key: str
    quote: str | None = None
    broken: bool = False  # a deliberate bad citation (the verifier must hide it)


@dataclass(frozen=True)
class ClaimT:
    """A claim template; ``{count}``, ``{query}`` and ``{since}`` come from the cited count row."""

    text: str
    refs: tuple[Ref, ...]
    section: str = "rationale"
    critic: tuple[str, str, str | None] | None = None  # Critic override: (status, reason, fix)


@dataclass(frozen=True)
class OppT:
    """An opportunity template; it is emitted only when every reference resolves in the input."""

    kind: str
    title: str
    one_line: str
    clusters: tuple[str, ...]  # cluster labels
    claims: tuple[ClaimT, ...]
    checklist: tuple[
        tuple[str, str | None], ...
    ] = ()  # (item, insight-text prefix proving it is in the vault)
    effort: str = "medium"
    next_steps: tuple[str, ...] = ()
    already_done_by: str | None = None  # the Critic finds this work does the same thing


LIGNIN_FOAMS, CNF, CEMENT, EMI, PFAS = (
    "Lignin polyurethane foams", "Cellulose nanofibre membranes", "Lignin in fibre-cement composites",
    "Wood composites for EMI shielding", "PFAS adsorption and photocatalytic degradation",
)  # fmt: skip
W_HIGH_LIGNIN = "W7204036258"
Q_LIGNIN_BARRIER = (
    "Replacing petroleum-derived polyols with lignin in polyurethane foams is essential for increasing renewable "
    "carbon utilization but is hindered by lignin’s heterogeneity, poor solubility, and limited reactivity."  # noqa: RUF001
)
Q_EUTECTIC = (
    "Here, we report a hydrogen-bond-engineered, eutectic-like polyol platform based on polyethylene glycol (PEG) and "
    "salicylic acid (SA), with ethylene carbonate (EC) as a cosolvent"
)
Q_CONTAINERS = (
    "Mass transfer of per- and polyfluoroalkyl substances (PFAS) to container walls during sample collection and "
    "storage or experimentation is an important source of error in PFAS analysis."
)
Q_MAGNETIC_WOOD = (
    "hierarchical and porous structured wood is used as the lightweight three‐dimensional organic scaffold for the "  # noqa: RUF001
    "incorporation of magnetic iron oxide nanoparticles"
)
Q_CONDUCTIVE_WOOD = (
    "Here, we develop a conductive wood as a new type of structural electromagnetic interference (EMI) shielding "
    "material with combined load-bearing function"
)
OWN_BDD = (
    "W4390531961"  # Structural dependence of PFAS oxidation in a boron doped diamond-electrochemical system
)

TEMPLATES: list[OppT] = [
    # ---- Foster + sample documents ----------------------------------------------------------------------------------
    OppT(
        "resubmission", "Resubmit lignin-rich upholstery foams with fire-safety and 50,000-cycle durability evidence",
        "Reviewers asked for EN 13501 fire data and a durability benchmark; recent polyol routes raise lignin content.",
        (LIGNIN_FOAMS,),
        (
            ClaimT("A reviewer found that the proposal did not demonstrate EN 13501 fire safety at the proposed "
                   "bio-content.",
                   (Ref("chunk", "The applicants do not demonstrate that the foams meet fire-safety standard EN 13501 "
                                 "at the proposed bio-content."),
                    Ref("insight", "Fire-safety compliance (EN 13501)")), "objection"),
            ClaimT("Another reviewer asked for a durability comparison with commercial petroleum-based foams over at "
                   "least 50,000 compression cycles.",
                   (Ref("chunk", "The durability plan lacks a comparison with commercial petroleum-based foams over at "
                                 "least 50,000 compression cycles."),), "objection"),
            ClaimT("Your preliminary foams with 15 % lignin already reached a compression set below 10 %.",
                   (Ref("chunk", "Preliminary foams with 15 % lignin reached a compression set below 10 %."),
                    Ref("insight", "Preliminary foams with 15 % lignin"))),
            ClaimT("You have published how lignin type and content influence the foaming dynamics and properties of "
                   "flexible polyurethane foams.",
                   (Ref("own", "W7164334760"),)),
            ClaimT("Lignin's heterogeneity, poor solubility and limited reactivity hinder high-lignin polyurethane "
                   "foams, and a eutectic-like polyol platform has been reported to address this.",
                   (Ref("work", W_HIGH_LIGNIN, Q_LIGNIN_BARRIER), Ref("work", W_HIGH_LIGNIN, Q_EUTECTIC))),
            ClaimT("{count} works since {since} match '{query}', so fire performance of lignin foams is a crowded "
                   "topic and the resubmission must state what is new.",
                   (Ref("count", LIGNIN_FOAMS),)),
        ),
        (("Lignin fractions characterised by 31P NMR and GPC", "31P NMR and GPC"),
         ("Preliminary foams with 15 % lignin and compression set below 10 %", "Preliminary foams with 15 % lignin"),
         ("EN 13501 reaction-to-fire tests at 30 % bio-content", None),
         ("50,000-cycle benchmark against a commercial petroleum-based foam", None)),
        "medium",
        ("Book EN 13501 reaction-to-fire tests for the 15 % and 30 % lignin foams",
         "Run a 50,000-cycle compression benchmark against a commercial foam",
         "Rewrite the aims around fire safety and durability for the next call"),
    ),
    OppT(
        "white_spot", "Sulfonated cellulose nanofibre separators for zinc-iodine flow cells beyond 1,000 hours",
        "Your 500-hour cycling data and published membrane put you ahead on a thinly measured separator topic.",
        (CNF,),
        (
            ClaimT("Your unpublished results show the sulfonated CNF membrane completing 500 hours of cycling at "
                   "20 mA cm-2 with coulombic efficiency above 98 %.",
                   (Ref("insight", "The sulfonated CNF membrane completed 500 hours"),)),
            ClaimT("Iodide crossover stayed below the UV-vis detection limit after 300 hours of cycling.",
                   (Ref("insight", "Iodide crossover stayed below"),)),
            ClaimT("You have already published a sulfonated cellulose nanofibre ion-selective membrane for zinc-iodine "
                   "redox flow batteries.",
                   (Ref("own", "W4412978443"),)),
            ClaimT("Cellulose nanofibres already reinforce cellulose acetate membranes, so membrane-grade CNF "
                   "processing is established.",
                   (Ref("work", "W4391533065", "In this study, nonwoven membranes were fabricated using cellulose "
                        "acetate (CA) as the primary component, with varying percentages of cellulose nanofibers "
                        "(CNFs) embedded as reinforcement."),),
                   critic=("unverified", "The abstract shows CNF reinforcement of cellulose acetate membranes, not "
                           "processing for flow-cell separators.",
                           "Cellulose nanofibres have been used to reinforce nonwoven cellulose acetate membranes.")),
            ClaimT("Publications and patents on nanocelluloses have been increasing every year.",
                   (Ref("work", "W3096818993", "The number of scientific publications and patents concerning "
                        "nanocelluloses has been increasing every year"),)),
            ClaimT("Only {count} works since {since} match '{query}', a thin literature for cellulose separators in "
                   "zinc-iodine cells.",
                   (Ref("count", CNF),)),
            ClaimT("Stability beyond 1,000 hours is still an open question in your own data.",
                   (Ref("insight", "Does the membrane remain stable beyond 1,000 hours?"),), "objection"),
        ),
        (("Cycling curves over 500 hours", "Cycling curves"),
         ("Impedance spectra at 0, 100, 300 and 500 hours", "Impedance spectra at 0, 100, 300 and 500 hours"),
         ("Flow-cell cycling set-up at 20 mA cm-2", "Flow-cell cycling at 20 mA cm-2"),
         ("Cells cycled beyond 1,000 hours", None),
         ("A membrane batch with reduced sulfonation degree", None)),
        "low",
        ("Extend the running cells to 1,000 hours with impedance checks every 250 hours",
         "Prepare a lower-sulfonation membrane batch and compare iodide crossover",
         "Draft a short communication on long-duration stability"),
    ),
    OppT(
        "synergy", "Map lignin domains in flexible polyurethane foams with EEM fluorescence",
        "Your fluorescence method for lignin domains in fibre-cement could explain lignin heterogeneity effects in foams.",
        (CEMENT, LIGNIN_FOAMS),
        (
            ClaimT("Lignin's heterogeneity, poor solubility and limited reactivity hinder its use as a polyol in "
                   "polyurethane foams.",
                   (Ref("work", W_HIGH_LIGNIN, Q_LIGNIN_BARRIER),)),
            ClaimT("You have published EEM fluorescence and DUV Raman analysis of lignin-emissive domains at "
                   "fibre-cement interfaces.",
                   (Ref("own", "W7214346167"),)),
            ClaimT("Your proposal aims to relate lignin type and hydroxyl content to foaming dynamics and cell "
                   "structure.",
                   (Ref("chunk", "Relate lignin type and hydroxyl content to foaming dynamics and cell structure."),
                    Ref("insight", "Relate lignin type and hydroxyl content"))),
            ClaimT("Only {count} works since {since} match '{query}', a small literature for lignin fluorescence "
                   "methods.",
                   (Ref("count", CEMENT),)),
        ),
        (("Lignin fractions characterised by 31P NMR and GPC", "31P NMR and GPC"),
         ("SEM and compression data on the foams", "SEM, compression testing"),
         ("EEM fluorescence measurements on foam sections", None)),
        "medium",
        ("Measure EEM fluorescence on foams made with kraft and organosolv lignin",
         "Correlate fluorescence maps with SEM cell structure",
         "Add the method as a work package in the resubmission"),
    ),
    OppT(
        "synergy", "Antimicrobial lignin-rich flexible foams for upholstery",
        "Antimicrobial lignin foams exist for wound care; your upholstery formulation could add that function.",
        (LIGNIN_FOAMS,),
        (
            ClaimT("Lignin-based polyurethane foams have been made in a one-step foaming method for antimicrobial "
                   "wound care.",
                   (Ref("work", "W4210817058", "Here, we propose a simple one-step foaming method to prepare "
                        "lignin-based polyurethane foams (LPUFs)"),
                    Ref("work", "W4210817058", "Antimicrobial materials are an urgent need for modern wound care in "
                        "the clinic."))),
            ClaimT("Your proposal targets flexible polyurethane foams with 30 % bio-content from fractionated kraft and "
                   "organosolv lignin.",
                   (Ref("insight", "Formulate flexible polyurethane foams with 30 % bio-content"),)),
            # A deliberate grounding error: the "quote" is the paper's title, but the paper has no abstract.
            ClaimT("High-performance flexible lignin polyurethane foams can be made in one pot with a green "
                   "co-solvent.",
                   (Ref("work", "W4402758757", "Green co-solvent-assisted one-pot synthesis of high-performance "
                        "flexible lignin polyurethane foam", broken=True),)),
            ClaimT("Durability against commercial petroleum-based foams over 50,000 cycles is not yet benchmarked.",
                   (Ref("insight", "Durability is not benchmarked"),), "objection"),
        ),
        (("Flexible foam formulation with fractionated lignin", "Formulate flexible polyurethane foams"),
         ("Antimicrobial testing partner", None)),
        "high",
        ("Screen a silver-free antimicrobial additive in the 15 % lignin foam",
         "Test antimicrobial activity on upholstery fabric standards"),
    ),
    OppT(
        "white_spot", "Lightweight magnetic wood panels for EMI shielding",
        "Magnetic and conductive wood scaffolds could give light EMI shielding panels.",
        (EMI,),
        (
            ClaimT("Porous wood scaffolds loaded with magnetic iron oxide nanoparticles have been used for "
                   "lightweight EMI shielding.",
                   (Ref("work", "W3033851578", Q_MAGNETIC_WOOD),)),
            ClaimT("Conductive wood has been developed as a structural EMI shielding material with a load-bearing "
                   "function.",
                   (Ref("work", "W3026932531", Q_CONDUCTIVE_WOOD),)),
            ClaimT("You have published bio-based magnetic and conductive wood for EMI shielding.",
                   (Ref("own", "W7119086364"),)),
        ),
        (), "medium", ("Compare panel density and shielding with the published magnetic wood",),
        already_done_by="W3033851578",
    ),
    # ---- Moreira, profile only --------------------------------------------------------------------------------------
    OppT(
        "white_spot", "Correct PFAS degradation tests for losses to container walls",
        "PFAS sorption to containers biases degradation results; your oxidation work could quantify it.",
        (PFAS,),
        (
            ClaimT("PFAS transfer to container walls during sampling, storage or experiments is an important source "
                   "of error in PFAS analysis.",
                   (Ref("work", "W4286511477", Q_CONTAINERS),)),
            ClaimT("Data that justify the usual polyethylene and polypropylene container choices are sparse.",
                   (Ref("work", "W4286511477", "However, there is sparse data to justify these container polymer "
                        "choices."),
                    Ref("work", "W4286511477", "To minimize this error, high-density polyethylene (HDPE) and "
                        "polypropylene containers are widely used for PFAS studies."))),
            ClaimT("You have studied how PFAS structure affects oxidation in a boron-doped diamond electrochemical "
                   "system.",
                   (Ref("own", OWN_BDD),)),
        ),
        (("Mass-balance protocol for PFAS degradation tests", None),),
        "low",
        ("Run blank container tests alongside the next electrochemical PFAS series",
         "Report recovered PFAS mass for each container polymer"),
    ),
    OppT(
        "synergy", "Magnetic iron-oxide wood as a recoverable photocatalyst support for PFAS degradation",
        "Magnetic wood scaffolds could hold your iron-oxide photocatalysts and be recovered after treatment.",
        (PFAS, EMI),
        (
            ClaimT("Porous wood has been used as a lightweight scaffold for magnetic iron oxide nanoparticles grown in "
                   "situ.",
                   (Ref("work", "W3033851578", Q_MAGNETIC_WOOD),)),
            ClaimT("You have developed hybrid graphenic and iron oxide photocatalysts for the decomposition of "
                   "synthetic chemicals.",
                   (Ref("own", "W4401793377"),)),
            ClaimT("Your Fe3O4/GO, ZnO and TiO2 thin films removed tetracycline photocatalytically under UV LED, blue "
                   "LED and solar light.",
                   (Ref("own", "W7163127579"),)),
        ),
        (("Iron-oxide photocatalyst synthesis", None), ("Wood scaffold supplier or partner", None)),
        "medium",
        ("Grow iron-oxide photocatalysts in delignified wood blocks",
         "Test magnetic recovery and activity over several PFAS cycles"),
    ),
    OppT(
        "synergy", "Conductive wood electrodes for electrochemical PFAS oxidation",
        "Conductive wood could be a low-cost electrode for the PFAS oxidation you studied on boron-doped diamond.",
        (PFAS, EMI),
        (
            ClaimT("Conductive wood has been made as a structural material with a load-bearing function.",
                   (Ref("work", "W3026932531", Q_CONDUCTIVE_WOOD),)),
            ClaimT("Metals and carbon-based compounds are the traditional conductor choices for shielding, flexible "
                   "electronics and energy storage.",
                   (Ref("work", "W3026932531", "Electric conductors are ubiquitously used for electromagnetic "
                        "shielding, flexible electronics, and energy storage, with metals and carbon-based compounds "
                        "as traditional choices for these applications."),)),
            ClaimT("You have studied the structural dependence of PFAS oxidation in a boron-doped diamond "
                   "electrochemical system.",
                   (Ref("own", OWN_BDD),)),
        ),
        (("Electrochemical PFAS oxidation cell", None),),
        "high",
        ("Prepare conductive wood electrodes and test their stability under anodic polarisation",
         "Compare PFOA oxidation rates with the boron-doped diamond baseline"),
    ),
    OppT(
        "white_spot", "Granular activated carbon adsorption of PFAS from drinking water",
        "Adsorption on activated carbon is the common PFAS removal route.",
        (PFAS,),
        (
            ClaimT("PFAS adsorb to surfaces during sampling and experiments, a known source of error in PFAS analysis.",
                   (Ref("work", "W4286511477", Q_CONTAINERS),)),
            ClaimT("You have studied PFAS oxidation in a boron-doped diamond electrochemical system.",
                   (Ref("own", OWN_BDD),)),
        ),
        (), "low", ("Compare activated carbon grades for short-chain PFAS",),
        already_done_by="W3180734447",
    ),
]  # fmt: skip
BY_TITLE = {t.title: t for t in TEMPLATES}


class Unresolved(Exception):
    """A template reference is absent from this input (the template belongs to another case)."""


def _fill(t: OppT, data: GapContext) -> dict[str, Any]:
    """The draft for ``t`` on this input, or ``Unresolved``."""
    by_label = {c.label: c.cluster_id for c in data.coverage}
    papers = {p.work_id: p for p in data.papers}
    own = {w.work_id: w for w in data.profile_summary.recent_works}

    def insight(prefix: str) -> str:
        hit = next((i.insight_ref for i in data.insights if i.text.startswith(prefix)), None)
        if hit is None:
            raise Unresolved(prefix)
        return hit

    def ref(r: Ref) -> tuple[dict[str, Any], dict[str, Any]]:
        if r.type == "work":
            p = papers.get(r.key)
            if p is None or r.quote is None:
                raise Unresolved(r.key)
            if not r.broken and norm(r.quote) not in norm(p.abstract):
                raise AssertionError(f"quote not verbatim in {r.key}: {r.quote!r}")
            return {"type": "work", "id": r.key, "quote": r.quote}, {}
        if r.type == "own":
            w = own.get(r.key)
            if w is None:
                raise Unresolved(r.key)
            return {"type": "work", "id": r.key, "quote": w.title}, {}
        if r.type == "insight":
            return {"type": "insight", "id": insight(r.key), "quote": None}, {}
        if r.type == "chunk":
            c = next((c for c in data.chunks if norm(r.key) in norm(c.text)), None)
            if c is None:
                raise Unresolved(r.key)
            return {"type": "chunk", "id": c.chunk_id, "quote": norm(r.key)}, {}
        if r.type == "count":
            cid = by_label.get(r.key)
            rows = [n for n in data.novelty if n.cluster_id == cid]
            if not rows:
                raise Unresolved(r.key)
            row = min(rows, key=lambda n: (len(n.query), n.candidate_id))  # the plain probe (P1)
            return {"type": "count", "id": row.candidate_id, "quote": None}, {
                "count": row.count,
                "query": row.query,
                "since": row.since_year,
            }
        raise ValueError(r.type)

    if any(label not in by_label for label in t.clusters):
        raise Unresolved(t.title)
    claims = []
    for c in t.claims:
        refs, values = [], {}
        for r in c.refs:
            out, vals = ref(r)
            refs.append(out)
            values.update(vals)
        claims.append({"text": c.text.format(**values), "section": c.section, "evidence_refs": refs})
    checklist = []
    for item, prefix in t.checklist:
        hit = next((i.insight_ref for i in data.insights if prefix and i.text.startswith(prefix)), None)
        checklist.append({"item": item, "present_in_vault": hit is not None, "ref": hit})
    return {
        "kind": t.kind, "title": t.title, "one_line": t.one_line, "cluster_ids": [by_label[x] for x in t.clusters],
        "claims": claims, "readiness_checklist": checklist, "effort_estimate": t.effort,
        "next_steps": list(t.next_steps),
    }  # fmt: skip


def analyse(data: GapContext) -> GapAnalysis:
    """Every template whose references all resolve in this batch, up to ``max_opportunities``."""
    drafts: list[dict[str, Any]] = []
    for t in TEMPLATES:
        if len(drafts) >= data.max_opportunities or t.title in data.exclude_titles:
            continue
        try:
            drafts.append(_fill(t, data))
        except Unresolved:
            continue
    return GapAnalysis.model_validate({"opportunities": drafts})


# ==== authoring: Critic ===========================================================================================
def critique(data: CriticInput) -> CriticVerdict:
    """Verified when the code checks passed and the claim's evidence is present, unless a template overrides it;
    ``already_done`` when the template names a paper that is in the input."""
    t = BY_TITLE.get(data.draft.title)
    overrides = {c.text.split("{")[0]: c.critic for c in t.claims if c.critic} if t else {}
    texts = {e.ref_id for e in data.evidence_texts}
    verdicts: list[ClaimVerdict] = []
    for c in data.draft.claims:
        override = next((v for k, v in overrides.items() if c.text.startswith(k)), None)
        if c.check_status != "passed":
            verdicts.append(ClaimVerdict(index=c.index, status="unverified", reason="failed code check"))
        elif not any(e in texts for e in c.evidence):
            verdicts.append(
                ClaimVerdict(index=c.index, status="unverified", reason="No verified evidence text.")
            )
        elif override is not None:
            status, reason, fix = override
            verdicts.append(ClaimVerdict(index=c.index, status=status, reason=reason, fix=fix))
        else:
            verdicts.append(ClaimVerdict(index=c.index, status="verified"))
    known = {p.work_id for p in data.similar_papers} | {
        e.ref_id for e in data.evidence_texts if e.type == "work"
    }
    done_by = t.already_done_by if t and t.already_done_by in known else None
    verified = sum(1 for v in verdicts if v.status == "verified")
    contradicted = any(v.status == "contradicted" for v in verdicts)
    keep = done_by is None and not contradicted and verified >= 2
    if done_by:
        reasons = [f"A similar paper ({done_by}) already does essentially the same thing."]
    elif keep:
        reasons = [f"{verified} claims verified against their evidence, including literature.",
                   "No similar paper combines these elements."]  # fmt: skip
    else:
        reasons = ["Fewer than two claims are supported by their evidence."]
    unverified = [v.index for v in verdicts if v.status == "unverified" and v.reason != "failed code check"]
    if unverified and len(reasons) < 3:
        reasons.append(f"Claim {unverified[0]} overreaches its evidence; narrower wording suggested.")
    return CriticVerdict(
        claims=verdicts, already_done=done_by is not None, already_done_by=done_by, keep=keep, reasons=reasons
    )


AUTHORS: dict[str, Callable[[Any], BaseModel]] = {
    "QueryPlanner": plan_queries,
    "ClusterLabeler": label_cluster,
    "InsightTopicTagger": tag_insights,
    "GapAnalyst": analyse,
    "Critic": critique,
}

# Edge cases served when no hash matches (``default.json``): the "insufficient evidence" answers. The planner default
# emits the recorded queries so any profile still gets a corpus offline. The labeller has no default (code falls back
# to topic names and records ``label_fallback``).
DEFAULTS: dict[str, dict[str, Any]] = {
    "QueryPlanner": {
        "queries": [{"search": s, "purpose": p, "because": "field:Bio-based materials", "max_results": 50}
                    for s, p, _ in PLAN]
    },
    "InsightTopicTagger": {"tags": []},
    "GapAnalyst": {"opportunities": []},
    "Critic": {"claims": [], "already_done": False, "already_done_by": None, "keep": False,
               "reasons": ["insufficient evidence"]},
}  # fmt: skip
DEFAULT_MODELS: dict[str, type[BaseModel]] = {
    "QueryPlanner": QueryPlan,
    "InsightTopicTagger": InsightTopicTags,
    "GapAnalyst": GapAnalysis,
    "Critic": CriticVerdict,
}


# ==== PostgreSQL parity ===========================================================================================
# Agent inputs carry floats read back from ``real`` (float4) columns: researcher_topic.weight and
# cluster.insight_weight / user_coverage / density. PostgreSQL returns the shortest text that round-trips in float4
# (0.34900284, not 0.349002849002849), so the in-memory run rounds the same way or every hash downstream differs.
def pg_real(x: float) -> float:
    """The value a PostgreSQL ``real`` column gives back for ``x``."""
    packed = struct.pack("<f", x)
    f32 = struct.unpack("<f", packed)[0]
    for digits in range(1, 10):
        candidate = float(f"{f32:.{digits}g}")
        if struct.pack("<f", candidate) == packed:
            return candidate
    return float(f32)


class PgLikeProfileSource:
    """``ProfileSource`` that returns topic weights as ``researcher_topic.weight`` (real) does, in SQL order."""

    def __init__(self, inner: ProfileSourceAdapter) -> None:
        self._inner = inner

    async def run_profile(self, owner_id: AccountId) -> RunProfile | None:
        """The inner profile with float4 weights, ordered by weight desc, topic id."""
        p = await self._inner.run_profile(owner_id)
        if p is None:
            return None
        topics = sorted(((i, n, s, pg_real(w)) for i, n, s, w in p.topics), key=lambda t: (-t[3], t[0]))
        return replace(p, topics=tuple(topics))


class _PgRealClusters(InMemoryClusterRepo):
    async def upsert_many(self, clusters: Sequence[Cluster]) -> None:
        await super().upsert_many(
            [
                replace(
                    c,
                    insight_weight=pg_real(c.insight_weight),
                    user_coverage=pg_real(c.user_coverage),
                    density=pg_real(c.density),
                )  # fmt: skip
                for c in clusters
            ]
        )


class PgLikeDiscoveryUoW(InMemoryDiscoveryUoW):
    """In-memory discovery unit of work whose cluster floats behave like the ``cluster`` table's ``real`` columns."""

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        self._clusters = _PgRealClusters(self._k)
        return self


# ==== running a case ==============================================================================================
class Recording:
    """``LLMPort`` around ``FakeLLM`` that records every discovery agent call (input, validated output)."""

    def __init__(self, inner: FakeLLM) -> None:
        self.inner = inner
        self.calls: list[tuple[str, BaseModel, BaseModel]] = []

    async def run(self, agent: AgentSpec[Any], input: Any, ctx: CallContext) -> AgentResult[Any]:
        """Delegate; keep the pair for the discovery agents."""
        result = await self.inner.run(agent, input, ctx)
        if agent.name in AGENTS:
            self.calls.append((agent.name, input, result.output))
        return result


def _samples() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "make_sample_docs", ROOT / "scripts" / "make_sample_docs.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class CaseRun:
    """What one case produced."""

    case: Case
    calls: list[tuple[str, BaseModel, BaseModel]]
    opportunities: list[Opportunity]
    status: str
    degraded: list[str]
    cost_usd: str
    llm_calls: int
    openalex_calls: int


async def run_case(case: Case, scripts: dict[str, Callable[[Any], BaseModel]] | None = None) -> CaseRun:
    """Claim, import, upload, ingest and run discovery in memory with the real services (fixed clock)."""
    s = Settings(_env_file=None, app_env="test")
    clock, ids = FixedClock(NOW), SequentialIdGenerator(1)
    fake = FakeLLM(OUT, InMemoryUsageMeter(), load_price_table(ROOT / s.llm_price_table), clock, ids)
    for name, fn in (AUTHORS if scripts is None else scripts).items():
        fake.script(name, fn)
    llm = Recording(fake)
    factory = AgentFactory(llm, PROMPTS, {"field_name": s.launch_field})
    lit = FixtureLiterature(OPENALEX)
    jobs = InMemoryJobQueue(clock, ids)
    pstore, vstore, dstore = ProfileStore(), VaultStore(), DiscoveryStore()

    def puow() -> InMemoryProfileUoW:
        return InMemoryProfileUoW(pstore)

    def vuow() -> InMemoryVaultUoW:
        return InMemoryVaultUoW(vstore)

    def duow() -> InMemoryDiscoveryUoW:
        return PgLikeDiscoveryUoW(dstore)

    profile = ProfileService(puow, lit, jobs, clock, ids)
    storage = InMemoryFileStorage()
    upload = UploadDocument(
        vuow, storage, jobs, clock, ids, RetentionPolicy.USER_CHOICE, s.vault_max_bytes, s.vault_max_documents
    )
    ingest = IngestDocument(
        vuow, storage, PdfDocxExtractor(),
        ExtractInsights(AgentsInsightExtractor(factory), s.insight_full_max_chars, s.insight_slice_chars,
                        s.insight_map_concurrency),
        clock,
    )  # fmt: skip
    psrc = PgLikeProfileSource(ProfileSourceAdapter(profile))
    vsrc = VaultSourceAdapter(VaultService(vuow, jobs, clock, ids))
    start = StartAnalysisRun(
        duow, jobs, psrc, vsrc, clock, ids,
        StartConfig(s.run_budget_usd, s.account_daily_budget_usd, s.run_max_per_day, s.run_max_openalex_calls,
                    s.run_max_corpus),
    )  # fmt: skip
    runner = RunAnalysis(
        duow, InMemoryLedger(dstore, clock), lit, AgentsDiscovery(factory), psrc, vsrc,
        load_field(ROOT / s.fields_config, s.launch_field),
        PipelineConfig(s.run_llm_concurrency, s.run_openalex_concurrency, s.run_analyse_max_calls,
                       max_resolve=s.run_max_resolve),
        clock,
    )  # fmt: skip
    handlers = {
        "profile.import": ImportProfile(puow, lit, clock, ids).handle,
        "vault.ingest": ingest.handle,
        "discovery.run": runner.handle,
    }

    async def drain() -> None:
        while (job := await jobs.claim("fixtures", list(handlers))) is not None:
            await jobs.complete(job.id, dict(await handlers[job.kind](job.payload, last_attempt=False)))

    await profile.claim(OWNER, case.author_ids, case.author_ids[0], None)
    await drain()
    samples = _samples()
    doc_ids: list[UUID] = []
    for path, retention in case.documents:
        src = ROOT / path
        pdf = samples.to_pdf(src.read_text(encoding="utf-8"))
        res = await upload.upload(OWNER, f"{src.stem}.pdf", pdf, retention == "keep", None)
        doc_ids.append(res.document.id)
    await drain()
    run = await start.start(OWNER, doc_ids)
    await drain()
    final = dstore.runs[run.id]
    opps = sorted(
        (o for o in dstore.opportunities.values() if o.run_id == run.id), key=lambda o: (o.batch, o.ord)
    )
    return CaseRun(
        case, llm.calls, opps, final.status.value, [d.reason for d in final.degraded], str(final.spend.cost_usd),
        final.spend.llm_calls, final.spend.openalex_calls,
    )  # fmt: skip


def check_run(r: CaseRun) -> list[str]:
    """Acceptance A1 on the in-memory run: done, bounds on shown, each shown fully grounded."""
    problems = []
    if r.status != "done":
        problems.append(f"run status {r.status}")
    if set(r.degraded) - ALLOWED_DEGRADED:
        problems.append(f"degraded {r.degraded}")
    shown = [o for o in r.opportunities if o.status is OppStatus.SHOWN]
    if not r.case.min_shown <= len(shown) <= r.case.max_shown:
        problems.append(f"{len(shown)} shown, expected {r.case.min_shown}-{r.case.max_shown}")
    for o in shown:
        ok, why = o.show_decision()
        ev = {
            (e.source_type, e.ref)
            for c in o.visible_claims()
            if c.section.value != "score"
            for e in c.evidence
        }
        lit = any(t is SourceType.LITERATURE for t, _ in ev)
        count = any(e.source_type is SourceType.COUNT for c in o.visible_claims() for e in c.evidence)
        if not (ok and len(ev) >= 2 and lit and count):
            problems.append(f"{o.title!r}: show={ok}/{why} evidence={len(ev)} literature={lit} count={count}")
        if any(c.status is not ClaimStatus.VERIFIED for c in o.visible_claims()):
            problems.append(f"{o.title!r}: a visible claim is not verified")
    return problems


# ==== files =======================================================================================================
def _dump(model: BaseModel | dict[str, Any]) -> str:
    data = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def wanted_files(runs: Sequence[CaseRun]) -> dict[Path, str]:
    """Every fixture file the runs need (outputs, canonical inputs, defaults)."""
    files: dict[Path, str] = {}
    for r in runs:
        for name, inp, out in r.calls:
            h = input_hash(inp)
            body = _dump(out)
            path = OUT / name / f"{h}.json"
            if path in files and files[path] != body:
                raise AssertionError(f"{path.name}: two different outputs for one input")
            files[path] = body
            files[OUT / name / f"{h}.input.json"] = (
                json.dumps(json.loads(canonical_json(inp)), indent=2, ensure_ascii=False, sort_keys=True)
                + "\n"
            )
    for name, raw in DEFAULTS.items():
        model = DEFAULT_MODELS[name]
        files[OUT / name / "default.json"] = _dump(model.model_validate(raw))
    return files


def summary(r: CaseRun) -> str:
    """One line per case."""
    by = {s.value: sum(1 for o in r.opportunities if o.status is s) for s in OppStatus}
    counts = ", ".join(f"{k}={v}" for k, v in by.items() if v)
    return (
        f"{r.case.id}: {r.status} degraded={r.degraded or '-'} {counts} llm_calls={r.llm_calls} "
        f"openalex_calls={r.openalex_calls} cost_usd={r.cost_usd}"
    )


def main() -> int:
    """Generate (or with ``--check`` compare) the fixtures; remove stale hash-named files."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--check", action="store_true", help="exit 1 if a fixture is missing, differs or is stale"
    )
    args = ap.parse_args()
    runs = [asyncio.run(run_case(c)) for c in load_cases()]
    bad = 0
    for r in runs:
        print(summary(r))
        for p in check_run(r):
            print(f"  PROBLEM {p}")
            bad += 1
    files = wanted_files(runs)
    for path, body in sorted(files.items()):
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != body:
                print(f"differs: {path.relative_to(ROOT)}")
                bad += 1
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
    for name in AGENTS:
        for stale in sorted((OUT / name).glob("*.json")):
            if _HASH_FILE.match(stale.name) and stale not in files:
                print(f"stale: {stale.relative_to(ROOT)}")
                bad += 1
                if not args.check:
                    stale.unlink()
    print(f"{sum(1 for p in files if not p.name.endswith('.input.json'))} fixture outputs")
    return 1 if bad and (args.check or any(check_run(r) for r in runs)) else 0


if __name__ == "__main__":
    sys.exit(main())
