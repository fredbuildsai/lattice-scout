"""SupabaseStorage against a mock transport (the live contract needs credentials)."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from tests.fakes import InMemoryUsageMeter, SequentialIdGenerator

from scout.adapters.storage.supabase import SupabaseStorage
from scout.ports.storage import StorageObjectMissing
from scout.shared import FixedClock, InfrastructureError


def _storage(handler: httpx.MockTransport) -> tuple[SupabaseStorage, InMemoryUsageMeter]:
    meter = InMemoryUsageMeter()
    http = httpx.AsyncClient(transport=handler)
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    return SupabaseStorage(
        http, "https://x.supabase.co", "svc", "vault", meter, clock, SequentialIdGenerator()
    ), meter


async def test_missing_object_is_404_or_400_with_a_404_body() -> None:
    for status, body in ((404, {}), (400, {"statusCode": "404", "error": "not_found"})):
        s, _ = _storage(httpx.MockTransport(lambda r, st=status, b=body: httpx.Response(st, json=b)))
        with pytest.raises(StorageObjectMissing):
            await s.get("vault/a/1")


async def test_a_rejected_key_is_an_infrastructure_error_not_a_missing_file() -> None:
    s, _ = _storage(
        httpx.MockTransport(
            lambda r: httpx.Response(400, json={"statusCode": "403", "error": "Unauthorized"})
        )
    )
    with pytest.raises(InfrastructureError) as caught:
        await s.get("vault/a/1")
    assert not isinstance(caught.value, StorageObjectMissing)


async def test_put_sends_upsert_and_records_a_metered_call_without_the_key() -> None:
    seen: list[httpx.Request] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        return httpx.Response(200, json={"Key": "vault/a/1"})

    s, meter = _storage(httpx.MockTransport(handler))
    await s.put("vault/a/1", b"data", "application/pdf")
    assert seen[0].headers["x-upsert"] == "true" and seen[0].headers["authorization"] == "Bearer svc"
    assert [c.endpoint for c in meter.external] == ["put"] and "vault/a" not in json.dumps(
        [c.model_dump(mode="json") for c in meter.external]
    )


async def test_server_errors_are_retried_then_raised() -> None:
    calls: list[int] = []

    def handler(r: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    s, _ = _storage(httpx.MockTransport(handler))
    with pytest.raises(InfrastructureError):
        await s.exists("vault/a/1")
    assert len(calls) == 3


def _bucket(n: int, *, delete_works: bool = True) -> tuple[httpx.MockTransport, dict[str, int]]:
    """A bucket with ``n`` objects under ``vault/a``; listing pages honour limit and offset."""
    names = [f"obj-{i:05d}" for i in range(n)]
    calls = {"list": 0, "delete": 0}

    def handler(r: httpx.Request) -> httpx.Response:
        body = json.loads(r.content or b"{}")
        if r.url.path.endswith("/object/list/vault"):
            calls["list"] += 1
            page = names[body["offset"] : body["offset"] + body["limit"]]
            return httpx.Response(200, json=[{"name": x, "id": f"id-{x}"} for x in page])
        if r.method == "DELETE":
            calls["delete"] += 1
            if delete_works:
                gone = {k.rsplit("/", 1)[1] for k in body["prefixes"]}
                names[:] = [x for x in names if x not in gone]
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    return httpx.MockTransport(handler), calls


async def test_list_prefix_follows_every_page() -> None:
    transport, calls = _bucket(2500)
    s, _ = _storage(transport)
    keys = await s.list_prefix("vault/a")
    assert len(keys) == 2500 and keys[0] == "vault/a/obj-00000" and calls["list"] >= 3


async def test_delete_prefix_removes_everything_and_reports_the_count() -> None:
    transport, calls = _bucket(2500)
    s, _ = _storage(transport)
    assert await s.delete_prefix("vault/a") == 2500
    assert await s.list_prefix("vault/a") == [] and calls["delete"] >= 1


async def test_delete_prefix_is_bounded_when_the_delete_has_no_effect() -> None:
    transport, calls = _bucket(3, delete_works=False)
    s, _ = _storage(transport)
    with pytest.raises(InfrastructureError):
        await s.delete_prefix("vault/a")
    assert calls["delete"] == 50


async def test_delete_prefix_raises_on_a_4xx() -> None:
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/object/list/vault"):
            return httpx.Response(200, json=[{"name": "x", "id": "1"}])
        return httpx.Response(403, json={})

    s, _ = _storage(httpx.MockTransport(handler))
    with pytest.raises(InfrastructureError):
        await s.delete_prefix("vault/a")
