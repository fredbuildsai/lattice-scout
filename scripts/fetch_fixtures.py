#!/usr/bin/env python3
"""Re-record the OpenAlex fixtures in tests/fixtures/openalex/ (needs network).

Usage:
    OPENALEX_API_KEY=... python scripts/fetch_fixtures.py [--out tests/fixtures/openalex]

Reads the existing manifest.json, replays every request against the live API and
rewrites the files in the same shape (subset of OpenAlex fields). Run it manually;
CI never calls it. Requires only the standard library.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.openalex.org"
WORK_FIELDS = "id,doi,display_name,title,publication_year,cited_by_count,type,primary_topic,topics,authorships"


def get(path: str, params: dict[str, str]) -> dict:
    """GET an OpenAlex endpoint with the API key and simple retry on 429/5xx."""
    key = os.environ.get("OPENALEX_API_KEY")
    if key:
        params = {**params, "api_key": key}
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 (fixed host)
                return json.load(r)
        except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
            if e.code in (429, 500, 502, 503) and attempt < 4:
                time.sleep(2**attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def fetch(entry: dict) -> dict:
    """Replay one manifest entry and return the response object to store."""
    m, p = entry["port_method"], entry["params"]
    if m == "get_author":
        return get(f"authors/{p['author_id']}", {})
    if m == "search_authors" and "orcid" in p:
        return get("authors", {"filter": f"orcid:{p['orcid']}"})
    if m == "search_authors":
        return get("authors", {"search": p["name"], "per_page": "10"})
    if m == "list_author_works":
        out, cursor = [], "*"
        while cursor:
            r = get("works", {"filter": f"author.id:{p['author_id']}", "per_page": "200",
                              "cursor": cursor, "select": WORK_FIELDS})
            out += r["results"]
            cursor = r["meta"].get("next_cursor") if r["results"] else None
        return {"meta": {"count": len(out), "per_page": 200, "next_cursor": None}, "results": out}
    if m == "search_works":
        flt = f"from_publication_date:{p['from_publication_date']},type:{p['type']}"
        return get("works", {"search": p["search"], "filter": flt, "per_page": str(p["per_page"]),
                             "select": WORK_FIELDS + ",abstract_inverted_index"})
    if m == "count_works":
        r = get("works", {"search": p["search"], "filter": f"from_publication_date:{p['from_publication_date']}",
                          "per_page": "1", "select": "id"})
        return {"meta": {"count": r["meta"]["count"]}, "results": []}
    raise ValueError(f"unknown port method {m}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tests/fixtures/openalex")
    out = Path(ap.parse_args().out)
    manifest = json.loads((out / "manifest.json").read_text())
    for entry in manifest["entries"]:
        obj = fetch(entry)
        (out / entry["file"]).write_text(json.dumps(obj, ensure_ascii=False, indent=1))
        print("ok", entry["file"])
    manifest["retrieved"] = time.strftime("%Y-%m-%d")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
