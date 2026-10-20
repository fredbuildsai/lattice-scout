"""Event catalogue: allowed event names and their property keys (mirrors frontend/src/analytics/events.ts)."""

from __future__ import annotations

from collections.abc import Mapping

MAX_STRING = 64

EVENT_CATALOGUE: Mapping[str, frozenset[str]] = {
    "page_view": frozenset(),
    "web_vital": frozenset({"metric", "value", "rating"}),
    "upload_started": frozenset({"kind"}),
    "upload_completed": frozenset({"document_id", "kind"}),
    "run_started": frozenset({"run_id", "documents"}),
    "run_viewed": frozenset({"run_id"}),
    "opportunity_opened": frozenset({"opportunity_id", "position"}),
    "evidence_marker_opened": frozenset({"opportunity_id", "claim_id"}),
    "evidence_source_clicked": frozenset({"opportunity_id", "evidence_id", "source_type"}),
    "opportunity_saved": frozenset({"opportunity_id"}),
    "opportunity_dismissed": frozenset({"opportunity_id"}),
    "feedback_given": frozenset({"target_type", "target_id", "rating"}),
    "match_viewed": frozenset({"match_id"}),
    "match_decision": frozenset({"match_id", "decision"}),
    "match_revealed": frozenset({"match_id"}),
    "match_rated": frozenset({"match_id", "rating"}),
    "menu_opened": frozenset(),
    "menu_item_clicked": frozenset({"item"}),
    "scout_fab_clicked": frozenset(),
}


def validate_event(name: str, properties: Mapping[str, object]) -> str | None:
    """Return a rejection reason, or None when the event is acceptable."""
    allowed = EVENT_CATALOGUE.get(name)
    if allowed is None:
        return "unknown event name"
    unknown = set(properties) - allowed
    if unknown:
        return f"unknown property: {sorted(unknown)[0]}"
    for key, value in properties.items():
        if isinstance(value, str) and len(value) > MAX_STRING:
            return f"property too long: {key}"
        if isinstance(value, (dict, list)):
            return f"property not scalar: {key}"
    return None
