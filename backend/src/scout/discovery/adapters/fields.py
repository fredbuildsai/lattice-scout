"""Load ``config/fields.yaml``."""

from __future__ import annotations

from pathlib import Path

import yaml

from scout.discovery.domain.field import FieldConfig
from scout.shared import ValidationFailed


def load_field(path: Path, key: str) -> FieldConfig:
    """Read the field ``key`` from the YAML file.

    Raises:
        ValidationFailed: unknown field or an invalid entry.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if key not in raw:
        raise ValidationFailed(f"Unknown field {key!r} in {path.name}")
    try:
        return FieldConfig.model_validate({"key": key, **raw[key]})
    except ValueError as exc:  # pydantic's message holds config values only, never user content
        raise ValidationFailed(f"Invalid field {key!r}: {exc}") from None
