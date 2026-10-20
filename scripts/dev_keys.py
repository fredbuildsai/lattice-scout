#!/usr/bin/env python3
"""Generate (or show) the dev ES256 key pair used by AUTH_MODE=dev. Keys live in var/dev-keys (git-ignored).

Usage: cd backend && uv run python ../scripts/dev_keys.py [DIR]
The API creates the keys on first start anyway; this script is for inspection and CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scout.identity.adapters.dev_auth import DevKeyPair

if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "var/dev-keys"
    pair = DevKeyPair.load_or_create(target)
    print(f"dev keys in {target}")
    print(json.dumps(pair.jwks(), indent=1))
