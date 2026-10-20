"""OpenAlexClient: the live ``LiteraturePort`` (httpx, API key, retries, response cache, call budget)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any, Protocol

import httpx
import structlog

from scout.adapters.literature.parse import parse_author, parse_work, short_id
from scout.ports.literature import AuthorCandidate, AuthorRecord, WorkQuery, WorkRecord
from scout.ports.usage import ExternalCallRecord, ExternalService, UsageMeterPort
from scout.shared import Clock, IdGenerator, InfrastructureError, NotFound, RateLimited
from scout.shared.context import get_context

log = structlog.get_logger()

API = "https://api.openalex.org"
WORK_FIELDS = (
    "id,doi,display_name,title,publication_year,cited_by_count,type,primary_topic,topics,authorships,"
    "abstract_inverted_index"
)
CACHE_TTL = timedelta(days=7)
RETRY_STATUS = {429, 500, 502, 503, 504}


class ResponseCache(Protocol):
    """Key/value cache for raw API responses (``openalex_cache`` table in production)."""

    async def get(self, key: str) -> dict[str, Any] | None:
        """Return a fresh cached response or None."""
        ...

    async def put(self, key: str, response: dict[str, Any]) -> None:
        """Store a response."""
        ...


class OpenAlexClient:
    """Implements ``LiteraturePort`` against api.openalex.org."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        clock: Clock,
        ids: IdGenerator,
        usage: UsageMeterPort,
        *,
        api_key: str | None = None,
        mailto: str | None = None,
        cache: ResponseCache | None = None,
        max_calls: int | None = None,
        max_retries: int = 4,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http, self._clock, self._ids, self._usage = http, clock, ids, usage
        self._key, self._mailto, self._cache = api_key, mailto, cache
        self._max_calls, self._max_retries, self._sleep = max_calls, max_retries, sleep
        self.calls = 0

    async def _get(self, path: str, params: dict[str, str], endpoint: str) -> dict[str, Any]:
        full = dict(sorted(params.items()))
        cache_key = hashlib.sha256(f"{path}?{json.dumps(full)}".encode()).hexdigest()
        if self._cache and (hit := await self._cache.get(cache_key)) is not None:
            self._record(endpoint, "ok", 200, 0, cache_hit=True)
            return hit
        if self._max_calls is not None and self.calls >= self._max_calls:
            raise RateLimited("OpenAlex call budget for this run is spent")
        query = dict(full)
        if self._key:
            query["api_key"] = self._key
        if self._mailto:
            query["mailto"] = self._mailto
        for attempt in range(self._max_retries + 1):
            started = time.perf_counter()
            self.calls += 1
            try:
                r = await self._http.get(f"{API}/{path}", params=query)
            except httpx.TimeoutException as exc:
                self._record(endpoint, "timeout", None, _ms(started))
                if attempt == self._max_retries:
                    raise InfrastructureError("OpenAlex timed out") from exc
                await self._sleep(2**attempt)
                continue
            except httpx.HTTPError as exc:
                self._record(endpoint, "error", None, _ms(started))
                raise InfrastructureError("OpenAlex unreachable") from exc
            if r.status_code in RETRY_STATUS and attempt < self._max_retries:
                self._record(endpoint, "error", r.status_code, _ms(started))
                await self._sleep(float(r.headers.get("retry-after", 2**attempt)))
                continue
            if r.status_code == 404:
                self._record(endpoint, "ok", 404, _ms(started))
                raise NotFound("Not found in OpenAlex")
            if r.status_code >= 400:
                self._record(endpoint, "error", r.status_code, _ms(started))
                raise InfrastructureError(f"OpenAlex returned {r.status_code}")
            data: dict[str, Any] = r.json()
            self._record(endpoint, "ok", r.status_code, _ms(started))
            if self._cache:
                await self._cache.put(cache_key, data)
            return data
        raise InfrastructureError("OpenAlex retries exhausted")  # pragma: no cover

    def _record(
        self, endpoint: str, status: str, http_status: int | None, latency_ms: int, *, cache_hit: bool = False
    ) -> None:
        ctx = get_context()
        self._usage.record_external_call(
            ExternalCallRecord(
                id=self._ids.new(),
                ts=self._clock.now(),
                account_id=ctx.account_id,
                session_id=None,
                run_id=ctx.run_id,
                service=ExternalService.OPENALEX,
                endpoint=endpoint,
                status=status,
                http_status=http_status,
                latency_ms=latency_ms,
                cache_hit=cache_hit,
            )  # fmt: skip
        )

    async def search_authors(self, name: str, affiliation_hint: str | None = None) -> list[AuthorCandidate]:
        """``/authors?search=``; hits whose institution matches the hint come first."""
        data = await self._get("authors", {"search": name, "per_page": "10"}, "/authors?search")
        out = [AuthorCandidate(**parse_author(a).model_dump()) for a in data["results"]]
        if affiliation_hint:
            hint = affiliation_hint.lower()
            out.sort(key=lambda c: not any(hint in i.lower() for i in c.institutions))
        return out

    async def authors_by_orcid(self, orcid: str) -> list[AuthorRecord]:
        """``/authors?filter=orcid:``."""
        data = await self._get("authors", {"filter": f"orcid:{orcid}"}, "/authors?filter=orcid")
        return [parse_author(a) for a in data["results"]]

    async def get_author(self, author_id: str) -> AuthorRecord:
        """``/authors/{id}``."""
        return parse_author(await self._get(f"authors/{short_id(author_id)}", {}, "/authors/{id}"))

    async def list_author_works(self, author_id: str, limit: int = 500) -> list[WorkRecord]:
        """Cursor-paged ``/works?filter=author.id:`` up to ``limit`` works."""
        works: list[WorkRecord] = []
        cursor: str | None = "*"
        while cursor and len(works) < limit:
            data = await self._get(
                "works",
                {"filter": f"author.id:{short_id(author_id)}", "per_page": "200", "cursor": cursor,
                 "select": WORK_FIELDS},
                "/works?filter=author.id",
            )  # fmt: skip
            works += [parse_work(w) for w in data["results"]]
            cursor = (data.get("meta") or {}).get("next_cursor") if data["results"] else None
        return works[:limit]

    def _work_params(self, q: WorkQuery) -> dict[str, str]:
        filters = []
        if q.from_publication_date:
            filters.append(f"from_publication_date:{q.from_publication_date}")
        if q.type:
            filters.append(f"type:{q.type}")
        params = {"search": q.search, "per_page": str(q.per_page)}
        if filters:
            params["filter"] = ",".join(filters)
        return params

    async def search_works(self, query: WorkQuery) -> list[WorkRecord]:
        """``/works?search=`` with filters."""
        data = await self._get("works", {**self._work_params(query), "select": WORK_FIELDS}, "/works?search")
        return [parse_work(w) for w in data["results"]]

    async def get_work(self, work_id_or_doi: str) -> WorkRecord | None:
        """``/works/{id or doi}`` or None."""
        key = work_id_or_doi if work_id_or_doi.startswith(("http", "doi:")) else short_id(work_id_or_doi)
        try:
            return parse_work(await self._get(f"works/{key}", {}, "/works/{id}"))
        except NotFound:
            return None

    async def count_works(self, query: WorkQuery) -> int:
        """``meta.count`` of a one-result search."""
        params = {**self._work_params(query), "per_page": "1", "select": "id"}
        data = await self._get("works", params, "/works?count")
        return int(data["meta"]["count"])


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
