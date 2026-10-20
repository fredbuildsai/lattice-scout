"""Acceptance: llm_call row via FakeLLM, 5 page views -> rows/views, partition handling."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from tests.conftest import TEST_URL
from tests.fakes import FakeLLM

from scout.adapters.observability.prices import load_price_table
from scout.adapters.observability.usage_meter import BufferedUsageMeter
from scout.adapters.persistence.db import Database
from scout.api.app import create_app
from scout.bootstrap import build_container
from scout.ports.llm import AgentSpec, CallContext, ModelTier
from scout.settings import Settings
from scout.shared import SystemClock, UuidGenerator

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]


class Pong(BaseModel):
    message: str


async def test_fake_llm_writes_llm_call_row(db: Database) -> None:
    meter = BufferedUsageMeter(db, flush_ms=50)
    await meter.start()
    prices = load_price_table(Path("config/llm_prices.yaml"))
    llm = FakeLLM(ROOT / "backend/tests/fixtures/llm", meter, prices, SystemClock(), UuidGenerator())
    spec = AgentSpec(
        name="Ping", prompt_version="1.0.0", tier=ModelTier.FAST, instructions="x" * 400, output_type=Pong
    )
    await llm.run(spec, {"q": "hello"}, CallContext(feature="test", account_id=UUID(int=1)))
    await meter.close()
    async with db.engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT input_tokens, output_tokens, cost_usd, feature, model FROM llm_call")
            )
        ).one()
    assert row.input_tokens > 0 and row.output_tokens > 0 and row.cost_usd > 0 and row.feature == "test"


def test_five_page_views_end_to_end(db: Database) -> None:
    settings = Settings(_env_file=None, app_env="test", database_url=TEST_URL, analytics_flush_ms=50)
    sid = str(uuid4())
    pages = ["/home", "/vault", "/runs", "/opportunities", "/matches"]
    events = [
        {
            "name": "page_view",
            "page": p,
            "prev_page": pages[i - 1] if i else None,
            "dwell_ms": 100 * i,
            "seq": i,
        }
        for i, p in enumerate(pages)
    ]
    with TestClient(create_app(build_container(settings))) as client:
        r = client.post(
            "/api/v1/events", json={"session": {"id": sid, "viewport": "390x844"}, "events": events}
        )
        assert r.status_code == 202

    # the lifespan exit flushed the buffers
    async def check() -> None:
        d = Database(TEST_URL)
        async with d.engine.connect() as conn:
            n = (
                await conn.execute(text("SELECT count(DISTINCT session_id), count(*) FROM analytics_event"))
            ).one()
            trans = (
                await conn.execute(text("SELECT count(*) FROM analytics.v_page_transitions"))
            ).scalar_one()
            path = (await conn.execute(text("SELECT pages FROM analytics.v_session_paths"))).scalar_one()
            sess = (await conn.execute(text("SELECT count(*) FROM app_session"))).scalar_one()
        await d.dispose()
        assert tuple(n) == (1, 5) and trans == 4 and path == pages and sess == 1

    asyncio.run(check())


async def test_partition_function_moves_rows_from_default(db: Database) -> None:
    far = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0) + timedelta(days=200)
    async with db.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO external_call (id, ts, service, endpoint, status, latency_ms) "
                "VALUES (gen_random_uuid(), :ts, 'openalex', '/works', 'ok', 5)"
            ),
            {"ts": far},
        )
        in_default = (
            await conn.execute(text("SELECT count(*) FROM ONLY external_call_default"))
        ).scalar_one()
        assert in_default == 1
        for _ in range(2):  # idempotent
            await conn.execute(
                text("SELECT public.scout_ensure_month_partition('external_call'::regclass, :m)"),
                {"m": far.date()},
            )
        in_default = (
            await conn.execute(text("SELECT count(*) FROM ONLY external_call_default"))
        ).scalar_one()
        total = (await conn.execute(text("SELECT count(*) FROM external_call"))).scalar_one()
    assert in_default == 0 and total == 1
