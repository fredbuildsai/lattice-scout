from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import BaseModel
from tests.fakes import InMemoryUsageMeter

from scout.adapters.llm.fake_llm import FakeFixtureMissing, FakeLLM
from scout.adapters.llm.prompts import load_agent_spec
from scout.ports.llm import AgentSpec, CallContext, LLMOutputInvalid, ModelTier
from scout.ports.usage import PriceTable
from scout.shared import FixedClock, SequentialIdGenerator, ValidationFailed

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "llm"
PROMPTS = Path(__file__).resolve().parents[4] / "prompts"


class Pong(BaseModel):
    message: str


def _spec(name: str = "Ping") -> AgentSpec[Pong]:
    return AgentSpec(
        name=name, prompt_version="1.0.0", tier=ModelTier.FAST, instructions="say pong", output_type=Pong
    )


def _llm(meter: InMemoryUsageMeter, fixtures: Path = FIX) -> FakeLLM:
    from decimal import Decimal

    from scout.ports.usage import ModelPrice

    prices = PriceTable(version="t", models={"fake": ModelPrice(input=Decimal(1), output=Decimal(2))})
    return FakeLLM(
        fixtures, meter, prices, FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), SequentialIdGenerator()
    )


async def test_fixture_run_records_one_call_with_context() -> None:
    meter = InMemoryUsageMeter()
    ctx = CallContext(feature="test", account_id=UUID(int=7), session_id=UUID(int=8), run_id=UUID(int=9))
    r = await _llm(meter).run(_spec(), {"q": "hi"}, ctx)
    assert r.output.message == "pong" and r.usage.input_tokens > 0 and r.cost_usd > 0
    [rec] = meter.llm
    assert (rec.account_id, rec.session_id, rec.run_id, rec.feature, rec.prompt_version) == (
        UUID(int=7), UUID(int=8), UUID(int=9), "test", "1.0.0",
    )  # fmt: skip
    assert rec.input_tokens == r.usage.input_tokens and rec.cost_usd == r.cost_usd


async def test_deterministic() -> None:
    a = await _llm(InMemoryUsageMeter()).run(_spec(), {"q": 1}, CallContext(feature="t"))
    b = await _llm(InMemoryUsageMeter()).run(_spec(), {"q": 1}, CallContext(feature="t"))
    assert a == b


async def test_missing_and_invalid_fixtures(tmp_path: Path) -> None:
    llm = _llm(InMemoryUsageMeter(), tmp_path)
    with pytest.raises(FakeFixtureMissing):
        await llm.run(_spec(), {}, CallContext(feature="t"))
    (tmp_path / "Ping").mkdir()
    (tmp_path / "Ping" / "default.json").write_text('{"wrong": 1}')
    with pytest.raises(LLMOutputInvalid):
        await llm.run(_spec(), {}, CallContext(feature="t"))


async def test_script_overrides_fixture() -> None:
    llm = _llm(InMemoryUsageMeter())
    llm.script("Ping", lambda i: {"message": f"echo {i['q']}"})
    r = await llm.run(_spec(), {"q": "x"}, CallContext(feature="t"))
    assert r.output.message == "echo x"


def test_load_agent_spec_checks_output_type() -> None:
    class CriticVerdict(BaseModel):
        pass

    spec = load_agent_spec(PROMPTS, "Critic", CriticVerdict)
    assert spec.prompt_version and spec.tier == ModelTier.REASONING and "sceptical" in spec.instructions
    with pytest.raises(ValidationFailed):
        load_agent_spec(PROMPTS, "Critic", Pong)
