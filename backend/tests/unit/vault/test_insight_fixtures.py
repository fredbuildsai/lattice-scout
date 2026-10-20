"""InsightExtractor fixtures: regenerated requests hit a fixture, outputs validate and every item verifies."""

import asyncio
import importlib.util
import json
import re
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from tests.fakes import FakeLLM, InMemoryUsageMeter

from scout.adapters.llm.factory import AgentFactory
from scout.ports.llm import CallContext
from scout.ports.usage import ModelPrice, PriceTable
from scout.shared import FixedClock, SequentialIdGenerator
from scout.vault.adapters.insight_extractor import AgentsInsightExtractor
from scout.vault.domain.insights import DocumentInsights, ItemStatus, Section, Stage
from scout.vault.domain.retention import MAX_QUOTE, retained_content
from scout.vault.domain.slicing import plan_slices
from scout.vault.domain.verification import verify

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "backend" / "tests" / "fixtures" / "llm"
_SPEC = importlib.util.spec_from_file_location(
    "make_insight_fixtures", ROOT / "scripts" / "make_insight_fixtures.py"
)
assert _SPEC and _SPEC.loader
gen = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = gen
_SPEC.loader.exec_module(gen)

NAMES = [s.filename for s in gen.samples()]
DECLINED = ["declined-proposal-lignin-foams.pdf", "sample-long-proposal-30p.pdf"]
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


@pytest.fixture(scope="module")
def cases() -> dict[str, Any]:
    """Render, extract and chunk every sample once (real PdfDocxExtractor in its child process)."""
    return {c.sample.filename: c for c in asyncio.run(gen.build_cases())}


@pytest.mark.parametrize("name", NAMES)
def test_fixture_exists_and_is_up_to_date(cases: dict[str, Any], name: str) -> None:
    case = cases[name]
    path = FIXTURES / "InsightExtractor" / f"{case.hash}.json"
    assert path.exists(), f"run: cd backend && uv run python ../scripts/make_insight_fixtures.py ({name})"
    assert json.loads(path.read_text(encoding="utf-8")) == case.output


@pytest.mark.parametrize("name", NAMES)
def test_output_validates_and_every_item_verifies(cases: dict[str, Any], name: str) -> None:
    case = cases[name]
    raw = DocumentInsights.model_validate(case.output)
    by_label = {c.label: c for c in case.chunks}
    items = verify(raw, by_label, Stage.FULL)
    assert [(i.section, i.reason) for i in items if i.status is not ItemStatus.VERIFIED] == []
    assert len(plan_slices(case.chunks)) == 1 and case.request.mode == "full"
    for quote in (q for r in retained_content(case.chunks, items).values() for q in r.quotes):
        assert len(quote.text) <= MAX_QUOTE and not quote.truncated
    assert not _EMAIL.search(json.dumps(case.output))


@pytest.mark.parametrize("name", DECLINED)
def test_declined_proposals_have_two_cited_objections(cases: dict[str, Any], name: str) -> None:
    out = DocumentInsights.model_validate(cases[name].output)
    assert out.kind == "declined_grant" and out.aims
    assert len(out.reviewer_objections) >= 2 and all(o.chunk_ids for o in out.reviewer_objections)


def test_long_proposal_is_one_full_call_citing_the_reviewer_page(cases: dict[str, Any]) -> None:
    case = cases["sample-long-proposal-30p.pdf"]
    pages = {c.label: (c.page_from, c.page_to) for c in case.chunks}
    out = DocumentInsights.model_validate(case.output)
    assert len(case.chunks) > 20 and sum(len(c.text) for c in case.chunks) <= gen.FULL_MAX_CHARS
    assert all(pages[o.chunk_ids[0]][1] >= 29 for o in out.reviewer_objections)


def test_budget_annex_is_the_empty_edge_case(cases: dict[str, Any]) -> None:
    out = DocumentInsights.model_validate(cases["sample-budget-annex.pdf"].output)
    assert out.kind == "other" and out.kind_reason
    assert not any(getattr(out, s.value) for s in Section) and out.keywords == []


def test_no_default_or_stale_fixture(cases: dict[str, Any]) -> None:
    files = {p.name for p in (FIXTURES / "InsightExtractor").glob("*.json")}
    assert files == {f"{c.hash}.json" for c in cases.values()}


async def test_real_adapter_over_fake_llm_finds_each_fixture(cases: dict[str, Any]) -> None:
    prices = PriceTable(version="t", models={"fake": ModelPrice(input=Decimal(1), output=Decimal(2))})
    llm = FakeLLM(
        FIXTURES,
        InMemoryUsageMeter(),
        prices,
        FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        SequentialIdGenerator(),
    )
    adapter = AgentsInsightExtractor(AgentFactory(llm, ROOT / "prompts"))
    for case in cases.values():
        outcome = await adapter.extract(case.request, CallContext(feature="vault.insights"))
        assert outcome.insights.model_dump(mode="json") == case.output
        assert outcome.prompt_version.startswith("InsightExtractor@")
