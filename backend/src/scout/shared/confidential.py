"""Data classes for agent inputs (ADR 016): published vs confidential, per field and per list item.

Pure and dependency-free (pydantic only), so domain models can classify their agent inputs. The routing policy
``input_for_route`` decides what a route that is or is not authorized for confidential data may receive.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from pydantic_core import PydanticUndefined


class DataClass(StrEnum):
    """Which kind of data a piece of agent input is (ADR 016, superseding the refusal rule of ADR 015).

    ``PUBLISHED``: OpenAlex works and profile fields the user made public; any endpoint may receive it.
    ``CONFIDENTIAL``: vault documents, unpublished text and anything derived from them; only authorized
    endpoints may receive it. Anything unclassified is confidential.
    """

    PUBLISHED = "published"
    CONFIDENTIAL = "confidential"


_TAG = "data_class"
_ITEMS = "confidential_items"
_DERIVED = "derived"


def published_field(**kwargs: Any) -> Any:
    """A field of an ``AgentInput`` holding published data only."""
    return Field(json_schema_extra={_TAG: DataClass.PUBLISHED.value}, **kwargs)


def confidential_field(**kwargs: Any) -> Any:
    """A field of an ``AgentInput`` holding confidential data. Give it a default (an empty list, ``None``) so
    the input still exists without it; a confidential field without a default makes the whole input
    confidential-only (the step is skipped when no authorized endpoint exists)."""
    return Field(json_schema_extra={_TAG: DataClass.CONFIDENTIAL.value}, **kwargs)


def mixed_items_field(**kwargs: Any) -> Any:
    """A list field whose elements classify themselves: each element exposes ``is_confidential``. Withholding
    drops the confidential elements and keeps the published ones."""
    return Field(json_schema_extra={_TAG: _ITEMS}, **kwargs)


def derived_field(**kwargs: Any) -> Any:
    """A field whose class follows its provenance: text that was written (by an agent) or selected (by code)
    while confidential material was in play. It is published when the input is not ``tainted`` and confidential
    when it is. Withholding resets it to its default; without a default the input has no published form and is
    not sent to an unauthorized route."""
    return Field(json_schema_extra={_TAG: _DERIVED}, **kwargs)


class AgentInput(BaseModel):
    """Base of every agent input model. Each field must be declared with ``published_field``,
    ``confidential_field``, ``mixed_items_field`` or ``derived_field``; a field without a declaration is rejected
    when the class is defined, and treated as confidential by ``withhold_confidential`` if it ever slips through.

    ``tainted`` is set by the step that builds the input when the run used confidential material at any point
    (provenance decides the class, not the field tag): every ``derived_field`` of a tainted input is confidential.
    It is not part of the serialized prompt input.
    """

    tainted: bool = Field(default=False, exclude=True, json_schema_extra={_TAG: DataClass.PUBLISHED.value})

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        missing = [n for n, f in cls.model_fields.items() if _tag(f) is None]
        if missing:
            raise TypeError(f"{cls.__name__}: classify {missing} as published or confidential (ADR 016)")


def _tag(info: Any) -> str | None:
    extra = info.json_schema_extra
    return extra.get(_TAG) if isinstance(extra, dict) else None


def contains_confidential(model: BaseModel) -> bool:
    """Whether any confidential part of ``model`` is non-empty. A non-``AgentInput`` model counts as confidential;
    an untagged field of any model counts as confidential; a derived field counts when the input is tainted. Pure."""
    if not isinstance(model, AgentInput):
        return True
    for name, info in type(model).model_fields.items():
        tag, value = _tag(info), getattr(model, name)
        if tag == DataClass.PUBLISHED.value:
            continue
        if tag == _DERIVED:
            if model.tainted and value:
                return True
        elif tag == _ITEMS:
            if value is None:
                continue
            if any(getattr(i, "is_confidential", True) for i in value):
                return True
        elif value:
            return True
    return False


def withhold_confidential[M: BaseModel](model: M) -> M:
    """A copy of ``model`` without any confidential part. Pure; the input is not modified.

    Raises:
        ValueError: ``model`` is not an ``AgentInput`` or a confidential field has no default, so no public form exists.
    """
    if not isinstance(model, AgentInput):
        raise ValueError("input is not classified")
    updates: dict[str, Any] = {}
    for name, info in type(model).model_fields.items():
        tag = _tag(info)
        if tag == DataClass.PUBLISHED.value or (tag == _DERIVED and not model.tainted):
            continue
        if tag == _ITEMS:
            updates[name] = [
                i for i in (getattr(model, name) or []) if not getattr(i, "is_confidential", True)
            ]
            continue
        default = info.get_default(call_default_factory=True)
        if default is PydanticUndefined:
            raise ValueError(f"confidential field {name} has no default")
        updates[name] = default
    updates["tainted"] = False
    return model.model_copy(update=updates)


def input_for_route(
    agent_class: DataClass, model: BaseModel, *, authorized: bool
) -> tuple[BaseModel, DataClass] | None:
    """The policy (ADR 016): what to send, and as which class, to a route that is or is not authorized.

    Returns ``(input, class)`` or ``None`` when the call has no published form and must be skipped. An
    authorized route receives the input unchanged (class ``CONFIDENTIAL`` if it carries confidential parts,
    else ``PUBLISHED``; ``agent_class`` does not matter, a non-``AgentInput`` always carries). An unauthorized
    route never receives a confidential part: they are withheld, and an input that cannot be classified or emptied (not an ``AgentInput``, required confidential field) is not
    sent at all.
    """
    del agent_class  # the parts decide, whatever the agent's declared class says (kept for the call sites)
    carries = contains_confidential(model)
    if not carries:
        return model, DataClass.PUBLISHED
    if authorized:
        return model, DataClass.CONFIDENTIAL
    try:
        return withhold_confidential(model), DataClass.PUBLISHED
    except ValueError:
        return None
