"""M5 acceptance end to end on PostgreSQL through the HTTP API and the real worker handlers (M5 review 1, BACKLOG F-40).

Two real accounts (Johan Foster and Raphaell Moreira, public OpenAlex records through the recorded fixtures) claim
their profiles, the profile jobs build the matching profiles, then:

* filters on (default): no proposal, because they are co-authors; reconnect mode (both exclusion filters off,
  threshold lowered, DECISIONS 83): one proposal;
* every pre-mutual body is anonymous: no name, institution, e-mail or ORCID anywhere;
* mutual path: A accepts (waiting), B accepts (revealed), the reveal job runs, and BOTH sides get the other's contact
  and 1-3 joint ideas written from their own point of view;
* decline path: B declines, A still sees ``waiting_on_them`` (nothing leaks), then ``expired`` after the deadline,
  and no contact was ever shared.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from tests.conftest import TEST_URL

from scout.adapters.observability.prices import resolve_path
from scout.api.app import create_app
from scout.bootstrap import build_container
from scout.matching.adapters.agents import AgentsMatching
from scout.matching.adapters.notifier import LoggingMatchNotifier
from scout.matching.adapters.sql import SqlMatchUnitOfWork
from scout.matching.adapters.vocabulary import load_vocabulary
from scout.matching.application.compute import ComputeMatches
from scout.matching.application.config import MatchConfig
from scout.settings import Settings
from scout.shared import FixedClock
from scout.worker.loop import build_handlers, run_once

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)  # the clock the matching fixtures were made with
FOSTER_EMAIL, MOREIRA_EMAIL = "johan.foster@example.edu", "r.moreira@example.edu"
FOSTER_IDS, MOREIRA_IDS = ["A5059588273", "A5140167166"], ["A5033675494"]
FORBIDDEN = ("foster", "moreira", "example.edu", "example university", "orcid", "ubc", "british columbia")


@dataclass
class World:
    client: TestClient
    clock: FixedClock
    settings: Settings
    foster: dict[str, str]
    moreira: dict[str, str]

    def drain(self) -> int:
        async def go() -> int:
            c = build_container(self.settings, clock=self.clock)
            handlers = build_handlers(c)
            n = 0
            while await run_once(c.job_queue, handlers, "flow-test"):
                n += 1
            await c.db.dispose()
            return n

        return asyncio.run(go())

    def compute(self, **cfg: Any) -> dict[str, Any]:
        """One matching run with the default filters, or with ``cfg`` overrides (reconnect mode)."""

        async def go() -> dict[str, Any]:
            c = build_container(self.settings, clock=self.clock)
            try:
                if not cfg:
                    return await c.compute_matches.execute(None)
                vocab = load_vocabulary(resolve_path(self.settings.methods_config))
                custom = replace(MatchConfig(field=self.settings.launch_field), **cfg)
                run = ComputeMatches(
                    lambda: SqlMatchUnitOfWork(c.db), AgentsMatching(c.agents), vocab, LoggingMatchNotifier(),
                    self.clock, custom,
                )  # fmt: skip
                return await run.execute(None)
            finally:
                await c.db.dispose()

        return asyncio.run(go())

    def cards(self, who: dict[str, str]) -> list[dict[str, Any]]:
        r = self.client.get("/api/v1/matches", headers=who)
        assert r.status_code == 200
        return list(r.json()["items"])

    def decide(self, who: dict[str, str], mid: str, action: str) -> dict[str, Any]:
        r = self.client.post(f"/api/v1/matches/{mid}/decision", json={"action": action}, headers=who)
        assert r.status_code == 200, r.text
        return dict(r.json())


def _login(client: TestClient, email: str) -> dict[str, str]:
    r = client.post("/api/v1/dev/login", json={"email": email})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def world(db: object, tmp_path: Path) -> Iterator[World]:
    clock = FixedClock(NOW)
    settings = Settings(
        _env_file=None, app_env="test", database_url=TEST_URL, local_storage_dir=tmp_path / "storage"
    )
    with TestClient(create_app(build_container(settings, clock=clock))) as client:
        w = World(client, clock, settings, _login(client, FOSTER_EMAIL), _login(client, MOREIRA_EMAIL))
        for who, ids in ((w.foster, FOSTER_IDS), (w.moreira, MOREIRA_IDS)):
            assert (
                client.patch("/api/v1/me/consent", json={"consent_matching": True}, headers=who).status_code
                == 200
            )
            claim = client.post(
                "/api/v1/profile/claim", json={"author_ids": ids, "primary_author_id": ids[0]}, headers=who
            )
            assert claim.status_code == 202
        w.drain()  # imports and matching profiles
        yield w


def _sql(q: str, **p: Any) -> list[tuple[Any, ...]]:
    eng = create_engine(TEST_URL)
    with eng.connect() as conn:
        rows = [tuple(r) for r in conn.execute(text(q), p)]
    eng.dispose()
    return rows


def _propose(w: World) -> str:
    assert w.compute()["proposed"] == 0  # co-authors: the default filters keep the pair apart
    assert w.cards(w.foster) == [] and w.cards(w.moreira) == []
    got = w.compute(exclude_coauthors=False, exclude_same_group=False, min_score=0.05)
    assert got["proposed"] == 1, got
    (fc,), (mc,) = w.cards(w.foster), w.cards(w.moreira)
    assert fc["id"] == mc["id"] and fc["kind"] == mc["kind"] == "anonymous"
    return str(fc["id"])


def _assert_anonymous(body: Any) -> None:
    blob = json.dumps(body).lower()
    for needle in FORBIDDEN:
        assert needle not in blob, needle
    assert "contact" not in blob and "display_name" not in blob and '"email"' not in blob
    assert "career_since" not in blob and "joint_works_count" not in blob


def test_default_filters_give_none_reconnect_gives_one_and_pre_mutual_bodies_are_anonymous(
    world: World,
) -> None:
    mid = _propose(world)
    for who in (world.foster, world.moreira):
        _assert_anonymous(world.cards(who))
        one = world.client.get(f"/api/v1/matches/{mid}", headers=who)
        assert one.status_code == 200 and one.json()["status"] == "awaiting_you"
        _assert_anonymous(one.json())
    stranger = _login(world.client, "someone.else@example.edu")
    assert world.client.get(f"/api/v1/matches/{mid}", headers=stranger).status_code == 404


def test_mutual_path_both_sides_get_contact_and_ideas_after_the_reveal_job(world: World) -> None:
    mid = _propose(world)
    first = world.decide(world.foster, mid, "accept")
    assert first["kind"] == "anonymous" and first["status"] == "waiting_on_them"
    _assert_anonymous(first)
    other = world.client.get(f"/api/v1/matches/{mid}", headers=world.moreira).json()
    assert other["status"] == "awaiting_you"  # B is not told that A accepted
    _assert_anonymous(other)

    second = world.decide(world.moreira, mid, "accept")
    assert second["kind"] == "revealed" and "Foster" in second["contact"]["display_name"]
    assert world.drain() >= 1  # matching.reveal
    assert _sql("SELECT ideas_status FROM match_proposal WHERE id = :m", m=mid) == [("ready",)]

    views = {n: world.client.get(f"/api/v1/matches/{mid}", headers=h).json() for n, h in
             (("foster", world.foster), ("moreira", world.moreira))}  # fmt: skip
    assert "Moreira" in views["foster"]["contact"]["display_name"]
    assert "Foster" in views["moreira"]["contact"]["display_name"]
    for v in views.values():
        assert v["kind"] == "revealed" and v["ideas_status"] == "ready"
        assert v["contact"]["email"].endswith("@example.edu")
        assert isinstance(v["career_since"], int) and (
            v["joint_works_count"] is None or v["joint_works_count"] > 0
        )
        assert "career_since" not in v["contact"] and "joint_works_count" not in v["contact"]
        assert 1 <= len(v["joint_ideas"]) <= 3
        for idea in v["joint_ideas"]:
            assert idea["title"] and idea["first_step"] and idea["uses_from_you"] and idea["uses_from_them"]
            # the other person's vault ids are never shown
            assert all(not i["source_refs"] for i in idea["uses_from_them"])
    # "from you" / "from them" are swapped between the two views of the same idea
    assert [i["title"] for i in views["foster"]["joint_ideas"]] == [
        i["title"] for i in views["moreira"]["joint_ideas"]
    ]
    f0, m0 = views["foster"]["joint_ideas"][0], views["moreira"]["joint_ideas"][0]

    def texts(items: list[dict[str, Any]]) -> list[str]:
        return [i["text"] for i in items]

    assert texts(f0["uses_from_you"]) == texts(m0["uses_from_them"])
    assert texts(f0["uses_from_them"]) == texts(m0["uses_from_you"])
    # each side reads the explanation written for it
    assert views["foster"]["why"] and views["moreira"]["why"]
    rating = world.client.post(f"/api/v1/matches/{mid}/rating", json={"stars": 5, "reasons": ["methods_fit"]},
                               headers=world.foster)  # fmt: skip
    assert rating.status_code == 200 and rating.json()["stars"] == 5
    # a thumb-like second call without reasons keeps them
    again = world.client.post(f"/api/v1/matches/{mid}/rating", json={"stars": 4}, headers=world.foster)
    assert again.json()["reasons"] == ["methods_fit"]
    assert world.client.post(f"/api/v1/matches/{mid}/rating", json={"stars": 4},
                             headers=_login(world.client, "someone.else@example.edu")).status_code == 404  # fmt: skip


def test_decline_path_leaks_nothing_and_ends_expired_without_contact(world: World) -> None:
    mid = _propose(world)
    world.decide(world.foster, mid, "accept")
    declined = world.decide(world.moreira, mid, "decline")
    assert declined["status"] == "declined_by_you"
    # A cannot tell: still waiting, anonymous, and nothing in the body changed except what A's own accept caused
    seen = world.client.get(f"/api/v1/matches/{mid}", headers=world.foster).json()
    assert seen["kind"] == "anonymous" and seen["status"] == "waiting_on_them"
    _assert_anonymous(seen)
    assert (
        world.client.get(f"/api/v1/matches/{mid}", headers=world.moreira).status_code == 404
    )  # hidden for the decliner
    assert world.drain() == 0  # no reveal job, no mail job to the decliner's counterpart
    # the deadline passes: the only thing A ever learns is "expired"
    world.clock.advance(timedelta(days=world.settings.match_expiry_days + 1))
    world.drain()
    foster = _login(world.client, FOSTER_EMAIL)  # the old token expired with the clock
    final = world.client.get(f"/api/v1/matches/{mid}", headers=foster).json()
    assert final["kind"] == "anonymous" and final["status"] == "expired"
    _assert_anonymous(final)
    assert _sql("SELECT count(*) FROM joint_idea") == [(0,)]


def test_withdrawn_matching_consent_closes_the_proposal_at_once(world: World) -> None:
    """M5 review 2: B accepts, then turns matching off; A's later accept never reveals B."""
    mid = _propose(world)
    world.decide(world.moreira, mid, "accept")
    off = world.client.patch("/api/v1/me/consent", json={"consent_matching": False}, headers=world.moreira)
    assert off.status_code == 200
    world.drain()  # matching.profile for B removes the profile and closes pending proposals
    assert _sql("SELECT state FROM match_proposal WHERE id = :m", m=mid) == [("declined",)]
    card = world.decide(world.foster, mid, "accept")
    assert card["kind"] == "anonymous" and card["status"] == "waiting_on_them"
    assert _sql("SELECT state FROM match_proposal WHERE id = :m", m=mid) == [("declined",)]
    world.clock.advance(timedelta(days=world.settings.match_expiry_days + 1))
    foster = _login(world.client, FOSTER_EMAIL)
    assert world.client.get(f"/api/v1/matches/{mid}", headers=foster).json()["status"] == "expired"
