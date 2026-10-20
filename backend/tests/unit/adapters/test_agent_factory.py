"""AgentFactory: placeholders, error mapping without leaking input, spans without text."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel
from structlog.testing import capture_logs
from tests.fakes import FakeLLM, InMemoryUsageMeter, SequentialIdGenerator

from scout.adapters.llm.factory import AgentFactory
from scout.ports.llm import CallContext, LLMOutputInvalid, LLMUnavailable
from scout.ports.usage import ModelPrice, PriceTable
from scout.shared import FixedClock, InfrastructureError, ValidationFailed

CANARY = "CANARY-vault-text-7f3a"


class Out(BaseModel):
    answer: str


PROMPT = """---
name: Echo
version: 2.1.0
tier: fast
output_type: Out
---
You work in {{field_name}} on {{today}}.
"""


@pytest.fixture
def setup(tmp_path: Path) -> tuple[AgentFactory, FakeLLM]:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "Echo.md").write_text(PROMPT)
    prices = PriceTable(version="t", models={"fake": ModelPrice(input=Decimal(1), output=Decimal(2))})
    llm = FakeLLM(
        tmp_path / "fx", InMemoryUsageMeter(), prices, FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        SequentialIdGenerator(),
    )  # fmt: skip
    return AgentFactory(llm, prompts, {"field_name": "materials", "today": "2026-01-01"}), llm


CTX = CallContext(feature="test")


def test_placeholders_filled_cached_and_version(setup: tuple[AgentFactory, FakeLLM]) -> None:
    factory, _ = setup
    spec = factory.spec("Echo", Out)
    assert "materials on 2026-01-01" in spec.instructions and "{{" not in spec.instructions
    assert factory.spec("Echo", Out) is spec
    assert AgentFactory.version(spec) == "Echo@2.1.0"


def test_unknown_placeholder_and_wrong_output_type(
    tmp_path: Path, setup: tuple[AgentFactory, FakeLLM]
) -> None:
    factory, llm = setup
    with pytest.raises(ValidationFailed, match="unfilled placeholder"):
        AgentFactory(llm, factory._dir, {"field_name": "x"}).spec("Echo", Out)
    with pytest.raises(ValidationFailed):
        factory.spec("Echo", BaseModel)


async def test_run_ok_logs_and_spans_carry_no_text(setup: tuple[AgentFactory, FakeLLM]) -> None:
    factory, llm = setup
    llm.script("Echo", {"answer": f"reply {CANARY}"})
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    factory = AgentFactory(llm, factory._dir, factory._placeholders, provider.get_tracer("test"))
    with capture_logs() as logs:
        result = await factory.run(factory.spec("Echo", Out), {"text": CANARY}, CTX)
    assert result.output.answer.startswith("reply")
    [span] = [s for s in exporter.get_finished_spans() if s.name == "agent.run"]
    attrs = dict(span.attributes or {})
    assert attrs["agent.name"] == "Echo" and attrs["agent.prompt_version"] == "Echo@2.1.0"
    assert int(str(attrs["agent.input_chars"])) > len(CANARY)
    assert CANARY not in json.dumps(attrs, default=str) + json.dumps(logs, default=str)
    assert any(entry["event"] == "agent_done" for entry in logs)


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        (
            {"wrong": f"field {CANARY}"},
            LLMOutputInvalid,
        ),  # schema mismatch: pydantic embeds the input in its error
        (lambda _inp: (_ for _ in ()).throw(InfrastructureError(f"upstream said {CANARY}")), LLMUnavailable),
        (lambda _inp: (_ for _ in ()).throw(ConnectionError(f"reset {CANARY}")), LLMUnavailable),
    ],
)
async def test_errors_are_mapped_and_do_not_leak(
    setup: tuple[AgentFactory, FakeLLM], script: Any, expected: type
) -> None:
    factory, llm = setup
    llm.script("Echo", script)
    with capture_logs() as logs, pytest.raises(expected) as caught:
        await factory.run(factory.spec("Echo", Out), {"text": CANARY}, CTX)
    exc = caught.value
    assert exc.__cause__ is None and exc.__suppress_context__
    assert CANARY not in str(exc) + repr(exc) + json.dumps(logs, default=str)
    assert any(entry["event"] == "agent_failed" for entry in logs)


async def test_programming_errors_propagate(setup: tuple[AgentFactory, FakeLLM]) -> None:
    factory, llm = setup

    def boom(_inp: Any) -> Any:
        raise TypeError("bug")

    llm.script("Echo", boom)
    with pytest.raises(TypeError):
        await factory.run(factory.spec("Echo", Out), {"text": "x"}, CTX)
