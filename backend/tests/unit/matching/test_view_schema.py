"""The card DTOs may never grow fields that reveal the other side's decision or identity before a reveal."""

from __future__ import annotations

from typing import Any

from scout.api.schemas.matching import MatchListOut
from scout.matching.domain.reveal import AnonymousMatchView, RevealedMatchView

FORBIDDEN = {
    "declined_by", "close_reason", "a_decision", "b_decision", "never_similar_by", "a_id", "b_id",
    "other_decision", "their_decision", "closed_at", "decline",
}  # fmt: skip
IDENTITY = {"contact", "email", "display_name", "institution", "orcid", "openalex_ids"}


def _keys(node: Any, out: set[str] | None = None) -> set[str]:
    out = set() if out is None else out
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            out.update(props)
        for v in node.values():
            _keys(v, out)
    elif isinstance(node, list):
        for v in node:
            _keys(v, out)
    return out


def test_no_view_exposes_the_other_sides_decision() -> None:
    for model in (AnonymousMatchView, RevealedMatchView, MatchListOut):
        assert not _keys(model.model_json_schema()) & FORBIDDEN, model.__name__


def test_identity_fields_exist_only_on_the_revealed_view() -> None:
    assert not _keys(AnonymousMatchView.model_json_schema()) & IDENTITY
    assert "contact" in _keys(RevealedMatchView.model_json_schema())
    defs = MatchListOut.model_json_schema()["$defs"]
    assert not _keys(defs["AnonymousMatchView"]) & IDENTITY
