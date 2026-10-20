"""Load the versioned price table (config/llm_prices.yaml)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from scout.ports.usage import ModelPrice, PriceTable

REPO_ROOT = Path(__file__).resolve().parents[5]


def resolve_path(path: Path) -> Path:
    """Return ``path`` if it exists from the cwd, else the same relative path under the repo root."""
    return path if path.is_absolute() or path.exists() else REPO_ROOT / path


def _price(raw: dict[str, Any]) -> ModelPrice:
    return ModelPrice(**{k: Decimal(str(v)) for k, v in raw.items()})


def load_price_table(path: Path) -> PriceTable:
    """Parse the YAML price table."""
    data = yaml.safe_load(resolve_path(path).read_text())
    return PriceTable(
        version=str(data["version"]),
        models={name: _price(p) for name, p in data["models"].items()},
        default=_price(data.get("default", {})),
    )
