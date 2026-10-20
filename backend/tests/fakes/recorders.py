"""Recording doubles for the metering and analytics ports."""

from __future__ import annotations

from scout.ports.analytics import AnalyticsEvent, SessionTouch
from scout.ports.usage import ExternalCallRecord, LlmCallRecord


class InMemoryUsageMeter:
    """Keeps every record in lists."""

    def __init__(self) -> None:
        self.llm: list[LlmCallRecord] = []
        self.external: list[ExternalCallRecord] = []

    def record_llm_call(self, rec: LlmCallRecord) -> None:
        self.llm.append(rec)

    def record_external_call(self, rec: ExternalCallRecord) -> None:
        self.external.append(rec)


class InMemoryAnalytics:
    """Keeps every event and session touch."""

    def __init__(self) -> None:
        self.events: list[AnalyticsEvent] = []
        self.sessions: list[SessionTouch] = []

    def track(self, evt: AnalyticsEvent) -> None:
        self.events.append(evt)

    def touch_session(self, s: SessionTouch) -> None:
        self.sessions.append(s)
