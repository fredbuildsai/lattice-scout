"""Load runtime agent prompts (prompts/*.md with front-matter) into ``AgentSpec``."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from scout.ports.llm import AgentSpec, DataClass, ModelTier
from scout.shared import ValidationFailed


def load_agent_spec[OutT: BaseModel](
    prompts_dir: Path, name: str, output_type: type[OutT], **overrides: object
) -> AgentSpec[OutT]:
    """Read ``<prompts_dir>/<name>.md``; the front-matter gives version and tier, the body the instructions.

    Raises:
        ValidationFailed: the file is malformed or declares a different ``output_type``.
    """
    text = (prompts_dir / f"{name}.md").read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValidationFailed(f"{name}.md has no front-matter")
    _, front, body = text.split("---", 2)
    meta = yaml.safe_load(front)
    if meta.get("name") != name:
        raise ValidationFailed(f"{name}.md declares name {meta.get('name')!r}")
    if meta.get("output_type") != output_type.__name__:
        raise ValidationFailed(f"{name}.md output_type {meta.get('output_type')!r} != {output_type.__name__}")
    return AgentSpec(
        name=name,
        prompt_version=str(meta["version"]),
        tier=ModelTier(meta["tier"]),
        instructions=body.strip(),
        output_type=output_type,
        data_class=DataClass(meta.get("data_class", "confidential")),
        **overrides,  # type: ignore[arg-type]
    )
