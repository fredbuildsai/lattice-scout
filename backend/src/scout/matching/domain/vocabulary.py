"""The controlled method vocabulary (loaded from ``config/methods.yaml`` by an adapter)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MethodTerm:
    """One method of the vocabulary."""

    id: str
    label: str
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class Vocabulary:
    """Versioned set of methods; the version is part of every profile hash."""

    version: str
    terms: tuple[MethodTerm, ...]

    def label(self, method_id: str) -> str | None:
        """Label of ``method_id``, or None when unknown."""
        return next((t.label for t in self.terms if t.id == method_id), None)

    def ids(self) -> frozenset[str]:
        """All method ids."""
        return frozenset(t.id for t in self.terms)
