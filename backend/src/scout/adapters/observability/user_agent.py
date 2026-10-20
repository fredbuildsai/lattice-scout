"""Coarse User-Agent parsing (family + device class). No personal data is kept."""

from __future__ import annotations

import re

_FAMILIES = [
    ("Edg/", "edge"),
    ("OPR/", "opera"),
    ("Firefox/", "firefox"),
    ("Chrome/", "chrome"),
    ("Safari/", "safari"),
]


def parse_user_agent(ua: str | None) -> tuple[str | None, str]:
    """Return ``(family, device_type)``."""
    if not ua:
        return None, "other"
    family = next((name for token, name in _FAMILIES if token in ua), "other")
    if re.search(r"iPad|Tablet", ua):
        device = "tablet"
    elif re.search(r"Mobi|iPhone|Android", ua):
        device = "mobile"
    elif re.search(r"Windows|Macintosh|X11|Linux", ua):
        device = "desktop"
    else:
        device = "other"
    return family, device
