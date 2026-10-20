from collections.abc import Iterator

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from scout.api.app import create_app
from scout.bootstrap import build_container
from scout.settings import Settings
from scout.shared import Conflict


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(build_container(Settings(_env_file=None, app_env="test")))
    r = APIRouter()

    @r.get("/boom-domain")
    async def boom_domain() -> None:
        raise Conflict("already there", extra={"field": "x"})

    @r.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals")

    @r.get("/need-int/{n}")
    async def need_int(n: int) -> int:
        return n

    app.include_router(r)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_health_and_request_id(client: TestClient) -> None:
    r = client.get("/health", headers={"X-Request-Id": "abc"})
    assert r.status_code == 200 and r.json() == {"status": "ok"} and r.headers["x-request-id"] == "abc"


def test_domain_error_is_problem_json(client: TestClient) -> None:
    r = client.get("/boom-domain")
    assert r.status_code == 409 and r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert (
        body["code"] == "conflict"
        and body["field"] == "x"
        and body["instance"] == "/boom-domain"
        and body["request_id"]
    )


def test_unhandled_error_hides_details(client: TestClient) -> None:
    r = client.get("/boom")
    assert r.status_code == 500 and "secret" not in r.text and r.json()["code"] == "internal_error"


def test_validation_error_is_problem_json(client: TestClient) -> None:
    r = client.get("/need-int/x")
    assert r.status_code == 422 and r.json()["code"] == "validation_failed" and r.json()["errors"]
