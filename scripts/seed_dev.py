"""Seed a local database for the matching demo (``make seed``).

* Two dev accounts, Johan Foster and Raphaell Moreira (public OpenAlex records through the fixtures), with matching
  consent and profiles built by the real jobs and the committed FakeLLM fixtures.
* 24 clearly synthetic researchers ("Synthetic Researcher NN (sample)", ``example.edu`` e-mails) with graded
  complementarity, inserted directly.
* A "reconnect" run for the demo pair first (co-authors at the same institution, lowered score threshold), then one
  matching run with the default filters over everyone.

Idempotent (deterministic ids) and refuses ``APP_ENV=prod``. Needs the dev database (``scripts/dev_db.sh``).
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from scout.adapters.observability.prices import resolve_path  # noqa: E402
from scout.identity.adapters.dev_auth import DevTokenIssuer  # noqa: E402
from scout.bootstrap import build_container  # noqa: E402
from scout.matching.adapters.agents import AgentsMatching  # noqa: E402
from scout.matching.adapters.notifier import LoggingMatchNotifier  # noqa: E402
from scout.matching.adapters.sql import SqlMatchUnitOfWork  # noqa: E402
from scout.matching.adapters.vocabulary import load_vocabulary  # noqa: E402
from scout.matching.application.compute import ComputeMatches  # noqa: E402
from scout.matching.application.config import MatchConfig  # noqa: E402
from scout.matching.domain.profile import MatchProfile, TopicWeight  # noqa: E402
from scout.settings import Settings  # noqa: E402
from scout.shared import AccountId, FixedClock  # noqa: E402
from scout.worker.loop import build_handlers, run_once  # noqa: E402

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)  # the clock the matching fixtures were made with


# Account ids are the ones dev login derives from the e-mail, so "log in as johan.foster@example.edu" reaches the
# seeded account instead of failing with EmailInUse.
def dev_id(email: str) -> AccountId:
    return AccountId(UUID(DevTokenIssuer.sub_for(email)))


FOSTER, MOREIRA = dev_id("johan.foster@example.edu"), dev_id("r.moreira@example.edu")
PEOPLE = {
    FOSTER: ("johan.foster@example.edu", ["A5059588273", "A5140167166"]),
    MOREIRA: ("r.moreira@example.edu", ["A5033675494"]),
}
SEED_NS = UUID("0b9a3f7e-52c1-5d84-9a6e-1f2c8d47b350")
COUNTRIES = ["DE", "FR", "BR", "CA", "JP", "ZA", "AU", "SE", "IN", "US"]
SYNTH = 24


def synth_id(n: int) -> AccountId:
    return dev_id(f"synth{n:02d}@example.edu")


async def real_people(c, settings: Settings) -> None:
    handlers = build_handlers(c)
    for acc, (email, authors) in PEOPLE.items():
        await c.identity.ensure_account(acc, email)
        await c.profile.claim(acc, authors, None, None)
    while await run_once(c.job_queue, handlers, "seed"):
        pass
    for acc in PEOPLE:
        await c.identity.update_consent(acc, True, False)
        print(f"profile {acc}: {await c.match_profile.execute(acc)}")


async def synthetic(c, settings: Settings) -> None:
    vocab = load_vocabulary(resolve_path(settings.methods_config))
    async with SqlMatchUnitOfWork(c.db) as uow:
        base = await uow.profiles.get(FOSTER)
    topics = (
        list(base.topics)
        if base and len(base.topics) >= 3
        else [TopicWeight(f"T{i}", f"Topic {i}", 1.0) for i in range(6)]
    )
    method_ids = [t.id for t in vocab.terms]
    async with c.db.engine.begin() as conn:
        for n in range(1, SYNTH + 1):
            acc, rid = synth_id(n), uuid5(SEED_NS, f"researcher:{n}")
            await conn.execute(
                text(
                    "INSERT INTO account (id, email, consent_matching, consent_vault_signals, launch_field, role,"
                    " created_at, updated_at) VALUES (:id, :e, true, false, :f, 'user', :t, :t)"
                    " ON CONFLICT (id) DO NOTHING"
                ),
                {"id": acc, "e": f"synth{n:02d}@example.edu", "f": settings.launch_field, "t": NOW},
            )
            await conn.execute(
                text(
                    "INSERT INTO researcher (id, account_id, display_name, institution, country)"
                    " VALUES (:r, :a, :n, :i, :c) ON CONFLICT (id) DO NOTHING"
                ),
                {"r": rid, "a": acc, "n": f"Synthetic Researcher {n:02d} (sample)",
                 "i": f"Example University {1 + n % 6}", "c": COUNTRIES[n % len(COUNTRIES)]},
            )  # fmt: skip
    for n in range(1, SYNTH + 1):
        shift = n % max(1, len(topics) - 2)
        chosen = topics[shift : shift + 3 + n % 3]
        methods = {method_ids[(2 * n + k) % len(method_ids)]: round(1.0 - 0.25 * k, 2) for k in range(3)}
        async with SqlMatchUnitOfWork(c.db) as uow:
            rid = uuid5(SEED_NS, f"researcher:{n}")
            await uow.profiles.save(
                MatchProfile(
                    account_id=synth_id(n), researcher_id=rid, field=settings.launch_field,
                    region=["europe", "south_america", "north_america", "asia", "africa", "oceania"][n % 6],
                    career_band=["early", "mid", "senior"][n % 3], activity=0.5 + (n % 3) / 4, eligible=True,
                    topics=tuple(chosen), methods=methods, method_sources={k: "abstract" for k in methods},
                    topics_hash=f"seed{n}", methods_hash=f"seed{n}", vocabulary_version=vocab.version,
                    methods_prompt_version="seed", uses_vault_signals=False, computed_at=NOW,
                ),
                [],
            )  # fmt: skip
            await uow.commit()
    print(f"{SYNTH} synthetic researchers ready")


async def demo_pair(c, settings: Settings) -> None:
    """Foster and Moreira are co-authors at one institution: propose them in reconnect mode.

    Runs before the synthetic researchers exist, so the pair is the only candidate and the weekly cap is not used up.
    """
    async with c.db.engine.begin() as conn:  # a re-seed starts from the same pool as the first one
        await conn.execute(
            text("DELETE FROM match_profile WHERE account_id = ANY(:ids)"),
            {"ids": [synth_id(n) for n in range(1, SYNTH + 1)]},
        )
    vocab = load_vocabulary(resolve_path(settings.methods_config))
    reconnect = replace(
        MatchConfig(field=settings.launch_field),
        exclude_coauthors=False,
        exclude_same_group=False,
        min_score=0.05,
    )
    demo = ComputeMatches(
        lambda: SqlMatchUnitOfWork(c.db),
        AgentsMatching(c.agents),
        vocab,
        LoggingMatchNotifier(),
        c.clock,
        reconnect,
    )
    print("reconnect run for the demo pair:", await demo.execute(FOSTER))


async def matching(c, settings: Settings) -> None:
    print("default run:", await c.compute_matches.execute(None))


async def main() -> None:
    settings = Settings()
    if settings.app_env == "prod":
        sys.exit("refusing to seed APP_ENV=prod")
    c = build_container(settings, clock=FixedClock(NOW))
    try:
        await real_people(c, settings)
        await demo_pair(c, settings)
        await synthetic(c, settings)
        await matching(c, settings)
    finally:
        await c.db.dispose()
    print(
        "\nLog in as a demo user (AUTH_MODE=dev):\n"
        '  curl -s -X POST localhost:8000/api/v1/dev/login -H "content-type: application/json" '
        '-d \'{"email": "johan.foster@example.edu"}\'\n'
        "  (also r.moreira@example.edu and synth01@example.edu ... synth24@example.edu)"
    )


if __name__ == "__main__":
    asyncio.run(main())
