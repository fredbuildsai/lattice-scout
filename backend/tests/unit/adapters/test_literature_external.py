"""Literature additions for external suggestions: last author and ``counts_by_year`` parsing, ``works_by_filter`` and
``get_authors`` on the OpenAlex client (request shape) and on the recorded fixtures (best effort)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from tests.fakes import InMemoryUsageMeter

from scout.adapters.literature.fixtures import FixtureLiterature
from scout.adapters.literature.openalex import OpenAlexClient
from scout.adapters.literature.parse import parse_author, parse_work
from scout.ports.literature import WorkFilter
from scout.shared import FixedClock, SequentialIdGenerator

ROOT = Path(__file__).resolve().parents[4] / "tests/fixtures/openalex"
FOSTER = "A5059588273"


def ship(aid: str, pos: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {"author": {"id": f"https://openalex.org/{aid}", "display_name": f"Name {aid}"}}
    if pos:
        out["author_position"] = pos
    return out


def test_parse_work_last_author() -> None:
    w = parse_work(
        {"id": "W1", "authorships": [ship("A1", "first"), ship("A2", "middle"), ship("A3", "last")]}
    )
    assert (w.last_author_id, w.last_author_name) == ("A3", "Name A3")
    solo = parse_work({"id": "W2", "authorships": [ship("A1", "first")]})
    assert solo.last_author_id is None  # a single author is "first" in OpenAlex: no PI signal
    old = parse_work({"id": "W3", "authorships": [ship("A1", None), ship("A2", None)]})
    assert old.last_author_id == "A2"  # recordings without positions: the last listed
    assert parse_work({"id": "W4"}).last_author_id is None


def test_parse_author_counts_by_year() -> None:
    a = parse_author(
        {"id": "A1", "display_name": "X", "counts_by_year": [{"year": 2026, "works_count": 3}, {}]}
    )
    assert [(c.year, c.works_count, c.cited_by_count) for c in a.counts_by_year] == [(2026, 3, 0)]


async def test_openalex_client_filter_and_author_lookup_requests() -> None:
    seen: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append({"path": req.url.path, **dict(req.url.params)})
        if req.url.path == "/authors":
            ids = req.url.params["filter"].removeprefix("openalex:").split("|")
            return httpx.Response(200, json={"results": [{"id": f"https://openalex.org/{i}"} for i in ids]})
        return httpx.Response(200, json={"results": [{"id": "W1", "authorships": [ship("A9", "last")]}]})

    c = OpenAlexClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), FixedClock(datetime(2026, 11, 17, tzinfo=UTC)),
        SequentialIdGenerator(), InMemoryUsageMeter(), mailto="x@example.edu",
    )  # fmt: skip
    works = await c.works_by_filter(
        WorkFilter(topic_ids=("T1", "https://openalex.org/T2"), from_publication_date="2023-11-17",
                   sort="cited_by_count:desc", page=3)
    )  # fmt: skip
    assert [w.last_author_id for w in works] == ["A9"]
    q = seen[-1]
    assert q["filter"] == "topics.id:T1|T2,from_publication_date:2023-11-17"
    assert q["sort"] == "cited_by_count:desc" and q["page"] == "3" and q["per_page"] == "100"
    assert "authorships" in q["select"]
    await c.works_by_filter(WorkFilter(author_id="A5"))
    assert seen[-1]["filter"] == "author.id:A5" and "sort" not in seen[-1]
    assert await c.works_by_filter(WorkFilter()) == []

    ids = [f"A{i}" for i in range(60)]
    got = await c.get_authors([*ids, "A1"])
    assert [a.id for a in got] == ids
    lookups = [s for s in seen if s["path"] == "/authors"]
    assert len(lookups) == 2 and lookups[0]["per_page"] == "50" and "counts_by_year" in lookups[0]["select"]
    await c.close()


async def test_fixture_literature_best_effort() -> None:
    fx = FixtureLiterature(ROOT)
    mine = await fx.works_by_filter(WorkFilter(author_id=FOSTER, from_publication_date="2020-01-01"))
    assert mine and all((w.year or 0) >= 2020 for w in mine)
    topic = next(t.id for w in mine for t in w.topics)
    topical = await fx.works_by_filter(WorkFilter(topic_ids=(topic,), sort="cited_by_count:desc", per_page=5))
    assert topical and all(topic in {t.id for t in w.topics} for w in topical)
    assert [w.cited_by_count for w in topical] == sorted((w.cited_by_count for w in topical), reverse=True)
    assert await fx.works_by_filter(WorkFilter()) == []
    got = await fx.get_authors([FOSTER, "A0"])
    assert [a.id for a in got] == [FOSTER]
