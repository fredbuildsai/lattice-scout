"""In-memory audit log."""

from __future__ import annotations

from scout.ports.audit import AuditEntry


class InMemoryAuditLog:
    """Collects entries; committed with the owning unit of work."""

    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    async def add(self, entry: AuditEntry) -> None:
        self.entries.append(entry)

    def actions(self) -> list[str]:
        return [e.action for e in self.entries]
