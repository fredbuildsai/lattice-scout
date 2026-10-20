#!/usr/bin/env python3
"""Write the ``FakeLLM`` fixtures of the ``InsightExtractor`` agent for the fictional sample vault documents.

For each sample (``tests/fixtures/vault/*.md``, the 30-page long proposal and a synthetic budget annex), the script
renders the PDF in memory, runs the real ``PdfDocxExtractor`` and ``chunk_pages``, builds the request exactly as
``AgentsInsightExtractor`` sends it (captured through a real ``AgentFactory``) and writes
``backend/tests/fixtures/llm/InsightExtractor/<input_hash>.json``.

The insight content below is hand-authored, but every ``chunk_ids`` list and every objection ``quote`` is located
programmatically in the real chunk text, so the fixtures always pass ``verify()``. Re-run after any change to the
sample documents, the renderer, the extractor, the chunker or the adapter's input model (the hash changes).

Usage: ``cd backend && uv run python ../scripts/make_insight_fixtures.py [--check]``
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import itertools
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

from pydantic import BaseModel, JsonValue

from scout.adapters.llm.factory import AgentFactory
from scout.adapters.llm.fake_llm import input_hash
from scout.ports.llm import AgentResult, AgentSpec, CallContext, TokenUsage
from scout.vault.adapters.extraction import PdfDocxExtractor
from scout.vault.adapters.insight_extractor import AgentsInsightExtractor
from scout.vault.domain.chunking import Chunk, chunk_pages
from scout.vault.domain.enums import DocumentMime
from scout.vault.domain.insights import DocumentInsights
from scout.vault.domain.slicing import plan_slices
from scout.vault.domain.verification import norm
from scout.vault.ports.insights import ChunkInput, InsightRequest

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"
OUT = ROOT / "backend" / "tests" / "fixtures" / "llm" / "InsightExtractor"
FULL_MAX_CHARS = 120_000  # Settings.insight_full_max_chars
_HASH_FILE = re.compile(r"^[0-9a-f]{16}\.json$")
_BANNER = "> SAMPLE DOCUMENT — fictional, written for Lattice Scout testing. Not authored by any real person."

BUDGET_ANNEX = f"""{_BANNER}

# Budget annex

## Staff
One postdoctoral researcher for 24 months and one laboratory technician for 6 months.

## Equipment
An upgrade of the existing rheometer and consumables for foam production.

## Travel
Two conference visits per year and one consortium meeting.
"""


def _samples() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "make_sample_docs", ROOT / "scripts" / "make_sample_docs.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- hand-authored content -------------------------------------------------------------------------------------
# ``at``: verbatim snippets of the source text; the chunks containing them become ``chunk_ids``.
# ``quote``: the reviewer passage; it is located in the chunks and emitted in its normalised (verbatim) form.

LIGNIN: dict[str, Any] = {
    "kind": "declined_grant",
    "kind_reason": "The title marks the proposal as declined and it ends with a panel summary and reviewer comments.",
    "title": "Lignin-rich flexible polyurethane foams for upholstery (proposal, declined)",
    "aims": [
        {
            "summary": "Formulate flexible polyurethane foams with 30 % bio-content using fractionated kraft and "
            "organosolv lignin.",
            "at": ["Formulate flexible foams with 30 % bio-content"],
        },
        {
            "summary": "Relate lignin type and hydroxyl content to foaming dynamics and cell structure.",
            "at": ["Relate lignin type and hydroxyl content to foaming dynamics"],
        },
        {
            "summary": "Demonstrate durability of lignin-rich foams under cyclic compression for upholstery use.",
            "at": ["Demonstrate durability under cyclic compression"],
        },
    ],
    "findings": [
        {
            "summary": "Preliminary foams with 15 % lignin reached a compression set below 10 %.",
            "at": ["Preliminary foams with 15 % lignin reached a compression set below 10 %"],
        },
    ],
    "methods": [
        {
            "name": "31P NMR and GPC",
            "detail": "Characterisation of the lignin fractions.",
            "at": ["Lignin fractions are characterised by 31P NMR and GPC"],
        },
        {
            "name": "Free-rise foaming",
            "detail": "Foam production route.",
            "at": ["Foams are produced in free-rise"],
        },
        {
            "name": "SEM, compression testing and dynamic mechanical analysis",
            "detail": "Analysis of foam structure and mechanical behaviour.",
            "at": ["analysed by SEM, compression testing and dynamic mechanical analysis"],
        },
    ],
    "datasets": [
        {
            "name": "Dataset linking lignin structure to foam properties",
            "status": "planned",
            "at": ["a dataset linking lignin structure to foam properties"],
        },
    ],
    "reviewer_objections": [
        {
            "quote": "The applicants do not demonstrate that the foams meet fire-safety standard EN 13501 at the "
            "proposed bio-content.",
            "summary": "Fire-safety compliance (EN 13501) at the proposed bio-content is not shown.",
        },
        {
            "quote": "The durability plan lacks a comparison with commercial petroleum-based foams over at least "
            "50,000 compression cycles.",
            "summary": "Durability is not benchmarked against commercial petroleum-based foams over 50,000 cycles.",
        },
        {
            "quote": "Strong science, but the application case is not yet convincing; fire behaviour and long-term "
            "durability must be addressed before funding.",
            "summary": "The panel wants fire behaviour and long-term durability addressed before funding.",
        },
    ],
    "open_questions": [
        {
            "summary": "Can foams with 30 % lignin bio-content pass the EN 13501 fire-safety standard?",
            "at": ["fire-safety standard EN 13501"],
        },
        {
            "summary": "How do lignin-rich foams compare with commercial petroleum-based foams over at least "
            "50,000 compression cycles?",
            "at": ["at least 50,000 compression cycles"],
        },
    ],
    "keywords": [
        "lignin",
        "flexible polyurethane foam",
        "kraft lignin",
        "organosolv lignin",
        "bio-based polyol",
        "upholstery",
        "fire safety",
        "EN 13501",
        "31P NMR",
        "compression set",
        "cyclic compression",
    ],
}

CNF: dict[str, Any] = {
    "kind": "unpublished_results",
    "kind_reason": "The title marks the document as unpublished results and it reports new cycling data.",
    "title": "Cycling of sulfonated cellulose nanofiber separators in zinc\u2013iodine flow cells (unpublished results)",
    "aims": [
        {
            "summary": "Evaluate the long-term cycling stability of the sulfonated CNF membrane in zinc\u2013iodine flow "
            "cells.",
            "at": ["we extended cycling tests to evaluate long-term stability"],
        },
    ],
    "findings": [
        {
            "summary": "The sulfonated CNF membrane completed 500 hours of cycling at 20 mA cm-2 with coulombic "
            "efficiency above 98 %.",
            "at": ["completed 500 hours of cycling at 20 mA cm-2"],
        },
        {
            "summary": "Iodide crossover stayed below the UV-vis detection limit after 300 hours.",
            "at": ["Iodide crossover remained below the detection limit"],
        },
        {
            "summary": "Membrane swelling in the electrolyte stabilised at 18 % after the first 24 hours.",
            "at": ["swelling in the electrolyte stabilised at 18 %"],
        },
    ],
    "methods": [
        {
            "name": "Flow-cell cycling at 20 mA cm-2",
            "detail": "Long-term stability test over 500 hours.",
            "at": ["500 hours of cycling at 20 mA cm-2"],
        },
        {
            "name": "UV-vis spectroscopy",
            "detail": "Detection of iodide crossover.",
            "at": ["UV-vis spectroscopy"],
        },
        {
            "name": "Impedance spectroscopy",
            "detail": "Spectra at 0, 100, 300 and 500 hours.",
            "at": ["impedance spectra at 0, 100, 300 and 500 hours"],
        },
        {
            "name": "SEM",
            "detail": "Imaging of the membrane surface after cycling.",
            "at": ["SEM images of the membrane surface after cycling"],
        },
    ],
    "datasets": [
        {"name": "Cycling curves", "status": "available", "at": ["Cycling curves"]},
        {
            "name": "Impedance spectra at 0, 100, 300 and 500 hours",
            "status": "available",
            "at": ["impedance spectra at 0, 100, 300 and 500 hours"],
        },
        {
            "name": "SEM images of the membrane surface after cycling",
            "status": "available",
            "at": ["SEM images of the membrane surface after cycling"],
        },
    ],
    "reviewer_objections": [],
    "open_questions": [
        {
            "summary": "Does the membrane remain stable beyond 1,000 hours?",
            "at": ["Does the membrane remain stable beyond 1,000 hours?"],
        },
        {
            "summary": "Can the sulfonation degree be reduced without increasing crossover?",
            "at": ["Can the sulfonation degree be reduced without increasing crossover?"],
        },
    ],
    "keywords": [
        "sulfonated cellulose nanofiber",
        "CNF membrane",
        "zinc\u2013iodine flow cell",
        "flow cell separator",
        "iodide crossover",
        "coulombic efficiency",
        "membrane swelling",
        "long-term cycling",
        "sulfonation degree",
        "impedance spectroscopy",
    ],
}

PFAS: dict[str, Any] = {
    "kind": "lab_notes",
    "kind_reason": "The title says lab notes and the entries are organised by week.",
    "title": "Lab notes: regenerating fungal-derived adsorption membranes after PFAS capture",
    "aims": [
        {
            "summary": "Regenerate fungal-derived adsorption membranes after PFAS capture without the solvent waste of "
            "methanol washing.",
            "at": ["regenerating fungal-derived adsorption", "the solvent waste is a problem for scale-up"],
        },
        {
            "summary": "Degrade captured PFAS directly on the membrane with a visible-light photocatalyst that does not "
            "damage the fungal matrix.",
            "at": ["degrade the captured PFAS directly on the membrane"],
        },
    ],
    "findings": [
        {
            "summary": "Fungal-derived membranes captured more than 90 % of PFOA from spiked tap water in batch tests.",
            "at": ["captured more than 90 % of PFOA from spiked tap water"],
        },
        {
            "summary": "Methanol regeneration recovered only 60 % of the capacity after three cycles.",
            "at": ["Regeneration with methanol recovered only 60 % of the capacity after three cycles"],
        },
    ],
    "methods": [
        {
            "name": "Batch adsorption tests",
            "detail": "PFOA-spiked tap water at 1 µg L-1.",
            "at": ["in batch tests"],
        },
        {
            "name": "Methanol regeneration",
            "detail": "Solvent wash over three cycles.",
            "at": ["Regeneration with methanol"],
        },
    ],
    "datasets": [],
    "reviewer_objections": [],
    "open_questions": [
        {
            "summary": "Which visible-light photocatalyst degrades captured PFAS without damaging the fungal matrix?",
            "at": ["photocatalyst that works under visible light"],
        },
        {
            "summary": "Is the membrane thermally stable up to 80 °C?",
            "at": ["Test thermal stability of the membrane up to 80 °C"],
        },
        {
            "summary": "Which partners with visible-LED photocatalysis experience for persistent organic pollutants "
            "could join?",
            "at": [
                "visible-LED photocatalysts for persistent organic pollutants",
                "Look for partners in photocatalytic water treatment",
            ],
        },
    ],
    "keywords": [
        "PFAS",
        "PFOA",
        "fungal-derived membrane",
        "adsorption membrane",
        "membrane regeneration",
        "visible-light photocatalysis",
        "on-membrane degradation",
        "water treatment",
        "persistent organic pollutants",
    ],
}

LONG: dict[str, Any] = {
    "kind": "declined_grant",
    "kind_reason": "The title marks the proposal as declined and it ends with a panel summary and reviewer comments.",
    "title": "Long sample proposal (declined)",
    "aims": [
        {
            "summary": "Study across 28 work packages how lignin hydroxyl content, set by the pulping process and "
            "fractionation route, affects reactivity towards isocyanates in foam formulations.",
            "at": ["Work package 1 Lignin is", "Work package 28 Lignin is"],
        },
    ],
    "findings": [],
    "methods": [
        {
            "name": "Lignin fractionation",
            "detail": "Fractionation route chosen alongside the pulping process.",
            "at": ["on the fractionation route"],
        },
    ],
    "datasets": [],
    "reviewer_objections": [
        {
            "quote": "The applicants do not demonstrate that the foams meet fire-safety standard EN 13501 at the "
            "proposed bio-content.",
            "summary": "Fire-safety compliance (EN 13501) at the proposed bio-content is not shown.",
        },
        {
            "quote": "Strong science, but the application case is not yet convincing.",
            "summary": "The panel finds the application case unconvincing.",
        },
    ],
    "open_questions": [
        {
            "summary": "Can the foams meet fire-safety standard EN 13501 at the proposed bio-content?",
            "at": ["fire-safety standard EN 13501"],
        },
    ],
    "keywords": [
        "lignin",
        "polyurethane foam",
        "hydroxyl content",
        "isocyanate reactivity",
        "lignin fractionation",
        "pulping process",
        "fire safety",
        "EN 13501",
    ],
}

# Edge case: a document with no research content gives empty lists (the "insufficient evidence" answer).
BUDGET: dict[str, Any] = {
    "kind": "other",
    "kind_reason": "The text lists budget lines only (staff, equipment, travel) and states no aims, methods or "
                   "results.",
    "title": "Budget annex",
    "aims": [], "findings": [], "methods": [], "datasets": [], "reviewer_objections": [], "open_questions": [],
    "keywords": [],
}  # fmt: skip


@dataclass(frozen=True)
class Sample:
    """One sample document and its hand-authored insights."""

    filename: str
    markdown: str
    content: dict[str, Any]


def samples() -> list[Sample]:
    """All sample documents, in a stable order."""
    m = _samples()
    src = Path(m.SOURCE)

    def md(stem: str) -> str:
        return (src / f"{stem}.md").read_text(encoding="utf-8")

    return [
        Sample("declined-proposal-lignin-foams.pdf", md("declined-proposal-lignin-foams"), LIGNIN),
        Sample("unpublished-results-cnf-membrane.pdf", md("unpublished-results-cnf-membrane"), CNF),
        Sample("lab-notes-pfas-membrane.pdf", md("lab-notes-pfas-membrane"), PFAS),
        Sample("sample-long-proposal-30p.pdf", m.long_proposal(), LONG),
        Sample("sample-budget-annex.pdf", BUDGET_ANNEX, BUDGET),
    ]


# --- locating labels and quotes ---------------------------------------------------------------------------------


def locate(snippet: str, chunks: list[Chunk]) -> tuple[list[str], str]:
    """Labels of the first chunk (or adjacent pair) whose normalised text contains ``snippet``, and the match.

    Raises:
        LookupError: the snippet is not in the document.
    """
    needle = norm(snippet)
    for c in chunks:
        if needle in norm(c.text):
            return [c.label], needle
    for a, b in itertools.pairwise(chunks):
        if needle in norm(f"{a.text} {b.text}"):
            return [a.label, b.label], needle
    raise LookupError(f"not found in the chunks: {snippet!r}")


def resolve(content: dict[str, Any], chunks: list[Chunk]) -> dict[str, Any]:
    """Replace ``at`` anchors by ``chunk_ids`` and normalise quotes; validate against ``DocumentInsights``."""
    out = {k: v for k, v in content.items() if not isinstance(v, list) or k == "keywords"}
    for section in ("aims", "findings", "methods", "datasets", "open_questions"):
        items = []
        for raw in content[section]:
            labels: list[str] = []
            for snippet in raw["at"]:
                labels += [x for x in locate(snippet, chunks)[0] if x not in labels]
            items.append({k: v for k, v in raw.items() if k != "at"} | {"chunk_ids": labels})
        out[section] = items
    objections = []
    for raw in content["reviewer_objections"]:
        labels, quote = locate(raw["quote"], chunks)
        objections.append({"quote": quote, "summary": raw["summary"], "chunk_ids": labels})
    out["reviewer_objections"] = objections
    return DocumentInsights.model_validate(out).model_dump(mode="json")


# --- the exact request ------------------------------------------------------------------------------------------


class _Capture:
    """``LLMPort`` that records the input the adapter sends and returns an empty result."""

    def __init__(self) -> None:
        self.inputs: list[BaseModel | dict[str, JsonValue]] = []

    async def run(self, agent: AgentSpec[Any], input: Any, ctx: CallContext) -> AgentResult[Any]:
        self.inputs.append(input)
        return AgentResult(
            output=DocumentInsights(), model="capture", route="fast", usage=TokenUsage(input_tokens=0, output_tokens=0),
            cost_usd=Decimal(0), latency_ms=0, attempts=1,
        )  # fmt: skip


def full_request(filename: str, chunks: list[Chunk]) -> InsightRequest:
    """The ``full``-mode request ``ExtractInsights`` builds for a document that fits in one slice."""
    slices = plan_slices(chunks, full_max=FULL_MAX_CHARS)
    if len(slices) != 1:
        raise ValueError(f"{filename}: {len(slices)} slices; map/reduce fixtures are not generated")
    return InsightRequest(
        mode="full", filename=filename, kind_hint=None,
        chunks=tuple(ChunkInput(chunk_id=c.label, page=c.page_from, text=c.text) for c in chunks),
    )  # fmt: skip


async def captured_input(req: InsightRequest) -> BaseModel | dict[str, JsonValue]:
    """The exact object ``AgentsInsightExtractor`` passes to the LLM port (it is what ``FakeLLM`` hashes)."""
    llm = _Capture()
    await AgentsInsightExtractor(AgentFactory(llm, PROMPTS)).extract(
        req, CallContext(feature="vault.insights")
    )
    return llm.inputs[0]


@dataclass(frozen=True)
class Case:
    """A generated fixture: the request, its chunks, the hash and the output."""

    sample: Sample
    chunks: list[Chunk]
    request: InsightRequest
    hash: str
    output: dict[str, Any]


async def build_cases() -> list[Case]:
    """Render, extract, chunk and build every request and fixture output (no files written)."""
    m = _samples()
    extractor = PdfDocxExtractor()
    cases = []
    for s in samples():
        text = await extractor.extract_text(m.to_pdf(s.markdown), DocumentMime.PDF)
        chunks = chunk_pages(UUID(int=0), text.pages)
        req = full_request(s.filename, chunks)
        h = input_hash(await captured_input(req))
        cases.append(Case(s, chunks, req, h, resolve(s.content, chunks)))
    return cases


def main() -> int:
    """Write (or with ``--check`` compare) the fixtures; remove stale hash-named files."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit 1 if a fixture is missing or differs")
    args = ap.parse_args()
    cases = asyncio.run(build_cases())
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = {f"{c.hash}.json" for c in cases}
    bad = 0
    for c in cases:
        path = OUT / f"{c.hash}.json"
        body = json.dumps(c.output, indent=2, ensure_ascii=False) + "\n"
        chars = sum(len(x.text) for x in c.chunks)
        print(f"{path.name}  {c.sample.filename}  mode=full chunks={len(c.chunks)} chars={chars}")
        if args.check:
            bad += not path.exists() or path.read_text(encoding="utf-8") != body
        else:
            path.write_text(body, encoding="utf-8")
    for stale in OUT.glob("*.json"):
        if _HASH_FILE.match(stale.name) and stale.name not in wanted:
            print(f"stale: {stale.name}")
            bad += 1
            if not args.check:
                stale.unlink()
    return 1 if args.check and bad else 0


if __name__ == "__main__":
    sys.exit(main())
