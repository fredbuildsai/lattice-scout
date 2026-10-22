"""Loader of ``config/methods.yaml``."""

from __future__ import annotations

from pathlib import Path

import yaml

from scout.matching.domain.vocabulary import MethodTerm, Vocabulary


def load_vocabulary(path: Path) -> Vocabulary:
    """Read the vocabulary file; a malformed file fails at startup."""
    data = yaml.safe_load(path.read_text())
    terms = tuple(
        MethodTerm(str(m["id"]), str(m["label"]), tuple(str(s) for s in m.get("synonyms", [])))
        for m in data["methods"]
    )
    if len({t.id for t in terms}) != len(terms):
        raise ValueError("duplicate method ids in the vocabulary")
    return Vocabulary(version=str(data["version"]), terms=terms)
