"""Author-name normalisation and compatibility (token based, order/accent/case insensitive)."""

from __future__ import annotations

import re
import unicodedata

_PAREN = re.compile(r"\([^)]*\)")
_SPLIT = re.compile(r"[\s.\-,]+")


def name_tokens(s: str) -> tuple[str, ...]:
    """NFKD, strip accents, casefold, drop ``(...)`` and digits, split on space . - ,"""
    s = unicodedata.normalize("NFKD", _PAREN.sub(" ", s))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    s = re.sub(r"\d+", " ", s)
    return tuple(t for t in _SPLIT.split(s) if t)


def names_compatible(a: str, b: str) -> bool:
    """True if two display names can denote the same person.

    Both need at least two tokens. Every token of the shorter name must match a distinct token of the longer
    (equal, or a one-letter token equal to the other's initial), with at least one equal match of length >= 2.
    """
    ta, tb = name_tokens(a), name_tokens(b)
    if len(ta) < 2 or len(tb) < 2:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    free = list(long_)
    strong = False
    for t in sorted(short, key=lambda x: -len(x)):  # match full tokens before initials
        match = next((c for c in free if c == t), None)
        if match is None:
            match = next(
                (c for c in free if (len(t) == 1 and c.startswith(t)) or (len(c) == 1 and t.startswith(c))),
                None,
            )
        elif len(t) >= 2:
            strong = True
        if match is None:
            return False
        free.remove(match)
    return strong
