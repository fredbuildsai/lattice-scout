"""Text helpers shared by contexts: verbatim-match normalisation and safe, bounded model/user strings."""

from __future__ import annotations

import re
import unicodedata

_DASHES = dict.fromkeys([*range(0x2010, 0x2016), 0x2212], "-")
_QUOTES = dict.fromkeys([0x2018, 0x2019, 0x201A, 0x201B], "'") | dict.fromkeys(
    [0x201C, 0x201D, 0x201E, 0x201F], '"'
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def norm(text: str) -> str:
    """Normalise for verbatim matching: NFKC, straight quotes, plain hyphens, joined hyphenation, single spaces."""
    t = unicodedata.normalize("NFKC", text).translate(_DASHES).translate(_QUOTES).replace("­", "")
    t = re.sub(r"-\s+", "-", t)
    return re.sub(r"\s+", " ", t).strip()


def clean_text(value: str, max_len: int = 600) -> str:
    """Strip control characters (a NUL breaks ``jsonb`` and Postgres then quotes the text in its error) and cap the
    length. Every string that comes from a model, a document or an external API goes through this before storage."""
    return _CONTROL.sub("", value)[:max_len]
