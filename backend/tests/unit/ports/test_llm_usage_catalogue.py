from decimal import Decimal

import pytest
from pydantic import ValidationError

from scout.ports.analytics_catalogue import validate_event
from scout.ports.llm import TokenUsage
from scout.ports.usage import ModelPrice, PriceTable


def test_token_usage_rejects_inconsistent_counts() -> None:
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=1, output_tokens=1, cached_input_tokens=2)
    with pytest.raises(ValidationError):
        TokenUsage(input_tokens=1, output_tokens=1, reasoning_tokens=2)


def test_token_usage_plus_keeps_none() -> None:
    a = TokenUsage(input_tokens=1, output_tokens=2)
    assert a.plus(a).cached_input_tokens is None
    b = TokenUsage(input_tokens=1, output_tokens=2, cached_input_tokens=1)
    assert a.plus(b).cached_input_tokens == 1


PRICES = PriceTable(
    version="t",
    models={
        "m": ModelPrice(
            input=Decimal(1), output=Decimal(2), cached_input=Decimal("0.1"), reasoning=Decimal(99)
        )
    },
)


def test_cost_formula_cached_and_reasoning_not_double_counted() -> None:
    u = TokenUsage(
        input_tokens=1_000_000, output_tokens=1_000_000, cached_input_tokens=500_000, reasoning_tokens=400_000
    )
    assert PRICES.cost("m", u) == Decimal("2.55000000")  # 0.5*1 + 0.5*0.1 + 1*2


def test_unknown_model_is_free_and_provider_prefix_is_stripped() -> None:
    u = TokenUsage(input_tokens=10, output_tokens=10)
    assert PRICES.cost("who/knows", u) == 0
    assert PRICES.price_for("openai/m")[1] is True


def test_catalogue() -> None:
    assert validate_event("page_view", {}) is None
    assert validate_event("nope", {}) == "unknown event name"
    assert validate_event("menu_item_clicked", {"x": 1}) == "unknown property: x"
    assert validate_event("menu_item_clicked", {"item": "x" * 65}) is not None
