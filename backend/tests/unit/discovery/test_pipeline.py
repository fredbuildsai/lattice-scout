"""The whole run pipeline against fakes: happy path, idempotence and the documented degradations."""

from __future__ import annotations

from tests.unit.discovery._world import OWNER, World

from scout.discovery.domain.enums import OppStatus, RunStatus, RunStep


async def test_happy_path_shows_opportunities_with_verified_evidence() -> None:
    w = World()
    run = await w.start()
    result = await w.run(run)
    final = w.get(run)
    assert final.status is RunStatus.DONE, (result, final.error_code, final.degraded)
    opps = list(w.store.opportunities.values())
    shown = [o for o in opps if o.status is OppStatus.SHOWN]
    assert shown, [(o.status, o.status_reason) for o in opps]
    for o in shown:
        visible = o.visible_claims()
        items = {(e.source_type, e.ref) for c in visible if c.section.value != "score" for e in c.evidence}
        assert len(items) >= 2
        assert o.show_decision()[0] and o.scores is not None and o.owner_id == OWNER
    assert final.percent() == 100 and final.spend.llm_calls == w.agents.calls.__len__()
    assert w.agents.count("QueryPlanner") == 1


async def test_a_second_execution_of_a_done_run_does_nothing() -> None:
    w = World()
    run = await w.start()
    await w.run(run)
    calls = len(w.agents.calls)
    assert await w.run(run) == {"skipped": "done"}
    assert len(w.agents.calls) == calls


async def test_each_step_is_recorded_and_the_first_step_ran_once() -> None:
    w = World()
    run = await w.start()
    await w.run(run)
    final = w.get(run)
    assert [s for s in RunStep if s in final.step_state] == list(RunStep)
