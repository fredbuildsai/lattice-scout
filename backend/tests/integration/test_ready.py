import os

import pytest
from fastapi.testclient import TestClient

from scout.api.app import create_app
from scout.bootstrap import build_container
from scout.settings import Settings

pytestmark = pytest.mark.integration
URL = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://scout:scout@localhost:5432/scout_test")


def test_ready_ok_with_database() -> None:
    with TestClient(
        create_app(build_container(Settings(_env_file=None, app_env="test", database_url=URL)))
    ) as c:
        assert c.get("/ready").status_code == 200


def test_ready_503_without_database() -> None:
    bad = "postgresql+psycopg://scout:scout@localhost:1/none"
    with TestClient(
        create_app(build_container(Settings(_env_file=None, app_env="test", database_url=bad)))
    ) as c:
        r = c.get("/ready")
        assert r.status_code == 503 and r.json()["code"] == "upstream_unavailable"
