"""Write the OpenAPI schema: ``python -m scout.api.export_openapi ../frontend/openapi.json``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scout.api.app import create_app


def main() -> None:
    """Dump the schema to the path given as the first argument (default: stdout)."""
    schema = json.dumps(create_app().openapi(), indent=2, sort_keys=True)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(schema + "\n")
    else:
        sys.stdout.write(schema + "\n")


if __name__ == "__main__":
    main()
