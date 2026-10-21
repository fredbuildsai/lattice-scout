#!/usr/bin/env python3
"""Re-record the OpenAlex fixtures in tests/fixtures/openalex/ (needs network).

Usage:
    OPENALEX_API_KEY=... python scripts/fetch_fixtures.py [--out tests/fixtures/openalex]

Add a public test subject (needs network; B7 in docs/BLOCKERS.md), two steps so a namesake is never picked:
    python scripts/fetch_fixtures.py --find "Frederick Chesneau" --affiliation Heidelberg   # prints candidates only
    python scripts/fetch_fixtures.py --add-author --openalex-id A<id>                       # records that exact id

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


def find_author(name: str, affiliation: str | None) -> None:
    """Print candidate authors (id, ORCID, institutions, works) without writing anything."""
    for a in get("authors", {"search": name, "per_page": "10"})["results"]:
        insts = [i["display_name"] for i in a.get("last_known_institutions") or []]
        hit = affiliation and any(affiliation.lower() in i.lower() for i in insts)
        print(f"{'*' if hit else ' '} {a['id'].rsplit('/', 1)[-1]}  {a['display_name']}  orcid={a.get('orcid')}"
              f"  works={a.get('works_count')}  {'; '.join(insts)}")
    print("Pick the id by hand, then rerun with --add-author --openalex-id <id>.")


def add_author(out: Path, author_id: str) -> None:
    """Record get_author, list_author_works and the topic names for one exact author id; manifest entries are idempotent."""
    manifest = json.loads((out / "manifest.json").read_text())
    entries = [
        {"port_method": "get_author", "params": {"author_id": author_id}, "file": f"authors/{author_id}.json"},
        {"port_method": "list_author_works", "params": {"author_id": author_id},
         "file": f"works_by_author/{author_id}.json"},
    ]
    topics_file = out / "topics.json"
    topics = json.loads(topics_file.read_text())
    for e in entries:
        obj = fetch(e)
        (out / e["file"]).parent.mkdir(parents=True, exist_ok=True)
        (out / e["file"]).write_text(json.dumps(obj, ensure_ascii=False, indent=1))
        for w in obj.get("results", []):
            for t in w.get("topics") or []:
                topics.setdefault(t["id"].rsplit("/", 1)[-1], {"name": t["display_name"],
                                                               "subfield": t["subfield"]["display_name"]})
        if e not in manifest["entries"]:
            manifest["entries"].append(e)
        print("ok", e["file"])
    topics_file.write_text(json.dumps(topics, ensure_ascii=False, indent=1))
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tests/fixtures/openalex")
    ap.add_argument("--find", help="author name: print candidates only")
    ap.add_argument("--affiliation", help="with --find: mark candidates whose institution contains this text")
    ap.add_argument("--add-author", action="store_true", help="record one author (needs --openalex-id)")
    ap.add_argument("--openalex-id", help="exact OpenAlex author id, e.g. A5012345678")
    args = ap.parse_args()
    out = Path(args.out)
    if args.find:
        return find_author(args.find, args.affiliation)
    if args.add_author:
        if not args.openalex_id or not args.openalex_id.startswith("A"):
            ap.error("--add-author needs --openalex-id A<digits> (use --find to look it up)")
        return add_author(out, args.openalex_id)
    manifest = json.loads((out / "manifest.json").read_text())
    for entry in manifest["entries"]:
        obj = fetch(entry)
        (out / entry["file"]).write_text(json.dumps(obj, ensure_ascii=False, indent=1))
        print("ok", entry["file"])
    manifest["retrieved"] = time.strftime("%Y-%m-%d")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
