"""MeteringCompletion / usage extraction without the llmrouter_free package."""

from types import SimpleNamespace
from typing import Any

import pytest

from scout.adapters.llm.llm_router import MeteringCompletion, _attempts, _local, _usage_from


def _resp(pin: int, pout: int, cached: Any = None, reasoning: Any = None) -> Any:
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=pin, completion_tokens=pout,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached) if cached is not None else None,
            completion_tokens_details=(
                SimpleNamespace(reasoning_tokens=reasoning) if reasoning is not None else None
            ),
        )
    )  # fmt: skip


def test_usage_from_reads_cached_and_reasoning_and_clamps() -> None:
    u = _usage_from(_resp(100, 20, cached=40, reasoning=5))
    assert (u.input_tokens, u.output_tokens, u.cached_input_tokens, u.reasoning_tokens) == (100, 20, 40, 5)
    clamped = _usage_from(_resp(10, 2, cached=99, reasoning=99))
    assert clamped.cached_input_tokens == 10 and clamped.reasoning_tokens == 2


def test_usage_from_missing_details_are_none_not_zero() -> None:
    u = _usage_from(_resp(10, 2))
    assert u.cached_input_tokens is None and u.reasoning_tokens is None


class _Boom(Exception):
    status_code = 429


def test_every_attempt_is_noted_including_failures() -> None:
    _local.attempts = []
    calls = iter([_Boom("x"), _resp(5, 1)])

    def completion(**_: Any) -> Any:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    m = MeteringCompletion(completion)
    with pytest.raises(_Boom):
        m(model="openai/a")
    m(model="openai/b")
    a, b = _attempts()
    assert (a.model, a.error_type, a.rate_limited) == ("openai/a", "_Boom", True)
    assert (b.model, b.usage.input_tokens, b.error_type) == ("openai/b", 5, None)
