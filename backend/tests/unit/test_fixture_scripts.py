"""The fixture generators are not run by ``make check``, so a port change can silently break them (ADR 016 did).

Each one is imported and a small case is run end to end through the real agent factory.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


async def test_discovery_fixture_generator_runs_a_case() -> None:
    mod = _load("make_discovery_fixtures")
    run = await mod.run_case(mod.load_cases()[0])
    assert run.status == "done" and run.calls and run.llm_calls > 0
    assert mod.check_run(run) == []


async def test_matching_fixture_generator_runs_the_pair() -> None:
    mod = _load("make_matching_fixtures")
    run = await mod.run_pair()
    assert run.calls and mod.check_run(run) == []


def test_every_recording_port_wrapper_answers_the_confidentiality_question() -> None:
    for name in ("make_discovery_fixtures", "make_matching_fixtures"):
        wrapper = _load(name).Recording
        assert callable(getattr(wrapper, "confidential_allowed", None)), name
