"""LiteraturePort contract (M1 task 3): FixtureLiterature offline; OpenAlexClient via MockTransport replaying the
same fixtures; the live API variant is ``needs_network``."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from tests.fakes import InMemoryUsageMeter

from scout.adapters.literature.fixtures import FixtureLiterature
from scout.adapters.literature.openalex import OpenAlexClient
from scout.ports.literature import LiteraturePort, WorkQuery
from scout.shared import FixedClock, NotFound, RateLimited, SequentialIdGenerator

ROOT = Path(__file__).resolve().parents[3] / "tests/fixtures/openalex"
T0 = datetime(2026, 1, 1, tzinfo=UTC)
FOSTER, SPLIT, MOREIRA = "A5059588273", "A5140167166", "A5033675494"
ORCID = "0000-0002-4103-8510"


def _transport() -> httpx.MockTransport:
    """Replay recorded responses through the HTTP layer (so the real parser/paging code runs)."""
    manifest = json.loads((ROOT / "manifest.json").read_text())["entries"]

    def handler(req: httpx.Request) -> httpx.Response:
        path, q = urlparse(str(req.url)).path, dict(req.url.params)
        if m := re.fullmatch(r"/authors/(\w+)", path):
            f = next(
                (
                    e["file"]
                    for e in manifest
                    if e["port_method"] == "get_author" and e["params"]["author_id"] == m[1]
                ),
                None,
            )
            return httpx.Response(200, json=json.loads((ROOT / f).read_text())) if f else httpx.Response(404)
        if path == "/authors":
            if "filter" in q:
                f = "authors_search/orcid-0000-0002-4103-8510.json"
            else:
                f = (
                    "authors_search/raphaell-moreira.json"
                    if "moreira" in q["search"].lower()
                    else "authors_search/johan-foster.json"
                )
            return httpx.Response(200, json=json.loads((ROOT / f).read_text()))
        if path == "/works":
            if "author.id" in q.get("filter", ""):
                aid = q["filter"].split("author.id:")[1]
                p = ROOT / f"works_by_author/{aid}.json"
                return (
                    httpx.Response(200, json=json.loads(p.read_text()))
                    if p.exists()
                    else httpx.Response(200, json={"meta": {"count": 0, "next_cursor": None}, "results": []})
                )
            if q.get("per_page") == "1":
                return httpx.Response(200, json=json.loads((ROOT / "works_count/n1.json").read_text()))
            return httpx.Response(200, json=json.loads((ROOT / "works_search/q1.json").read_text()))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _fixture() -> LiteraturePort:
    return FixtureLiterature(ROOT)


def _client() -> LiteraturePort:
    return OpenAlexClient(
        httpx.AsyncClient(transport=_transport()), FixedClock(T0),
        SequentialIdGenerator(), InMemoryUsageMeter(), api_key="k", mailto="x@example.edu",
    )  # fmt: skip


MAKERS: list[Any] = [pytest.param(_fixture, id="fixtures"), pytest.param(_client, id="openalex-replay")]


@pytest.fixture(params=MAKERS)
def lit(request: pytest.FixtureRequest) -> LiteraturePort:
    make: Callable[[], LiteraturePort] = request.param
    return make()


async def test_search_authors_includes_split_records(lit: LiteraturePort) -> None:
    hits = await lit.search_authors("Johan Foster")
    ids = {h.id for h in hits}
    assert {FOSTER, SPLIT} <= ids
    main = next(h for h in hits if h.id == FOSTER)
    assert (
        main.orcid == ORCID
        and main.works_count == 159
        and "University of British Columbia" in main.institutions
    )


async def test_authors_by_orcid_groups_split_profiles(lit: LiteraturePort) -> None:
    assert {a.id for a in await lit.authors_by_orcid(ORCID)} == {FOSTER, SPLIT}


async def test_get_author(lit: LiteraturePort) -> None:
    a = await lit.get_author(FOSTER)
    assert a.display_name == "Earl Johan Foster" and a.h_index == 50 and a.topics and a.country == "CA"
    with pytest.raises(NotFound):
        await lit.get_author("A0000000000")


async def test_list_author_works(lit: LiteraturePort) -> None:
    works = await lit.list_author_works(FOSTER)
    assert len(works) == 159
    w = works[0]
    assert w.id.startswith("W") and w.title and w.year and not (w.doi or "").startswith("http")
    assert len(await lit.list_author_works(FOSTER, limit=10)) == 10


async def test_search_and_count_works(lit: LiteraturePort) -> None:
    q = WorkQuery(
        search="lignin polyurethane foam", from_publication_date="2020-01-01", type="article", per_page=6
    )
    found = await lit.search_works(q)
    assert found and all(w.abstract for w in found[:1])
    assert (
        await lit.count_works(
            WorkQuery(search="lignin polyurethane foam flame retardant", from_publication_date="2021-01-01")
        )
        == 1731
    )


async def test_fixture_get_work_by_id() -> None:
    fx = FixtureLiterature(ROOT)
    work = (await fx.list_author_works(FOSTER))[0]
    assert (await fx.get_work(work.id)) == work
    assert await fx.get_work("W0") is None


async def test_openalex_retries_429_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json=json.loads((ROOT / "authors/A5059588273.json").read_text()))

    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    meter = InMemoryUsageMeter()
    c = OpenAlexClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        FixedClock(T0),
        SequentialIdGenerator(),
        meter,
        sleep=sleep,
    )
    assert (await c.get_author(FOSTER)).id == FOSTER
    assert (
        calls["n"] == 3 and len(slept) == 2 and [m.status for m in meter.external] == ["error", "error", "ok"]
    )


async def test_openalex_call_budget_and_cache() -> None:
    store: dict[str, dict[str, Any]] = {}

    class Cache:
        async def get(self, key: str) -> dict[str, Any] | None:
            return store.get(key)

        async def put(self, key: str, response: dict[str, Any]) -> None:
            store[key] = response

    meter = InMemoryUsageMeter()
    c = OpenAlexClient(
        httpx.AsyncClient(transport=_transport()), FixedClock(T0),
        SequentialIdGenerator(), meter, cache=Cache(), max_calls=1,
    )  # fmt: skip
    await c.get_author(FOSTER)
    await c.get_author(FOSTER)  # served from cache, not counted
    assert c.calls == 1 and [m.cache_hit for m in meter.external] == [False, True]
    with pytest.raises(RateLimited):
        await c.get_author(MOREIRA)


@pytest.mark.needs_network
async def test_live_openalex_search() -> None:
    import os

    async with httpx.AsyncClient(timeout=20) as http:
        c = OpenAlexClient(
            http,
            FixedClock(T0),
            SequentialIdGenerator(),
            InMemoryUsageMeter(),
            api_key=os.environ.get("OPENALEX_API_KEY"),
        )
        hits = await c.search_authors("Johan Foster")
    assert any(h.orcid == ORCID for h in hits)
