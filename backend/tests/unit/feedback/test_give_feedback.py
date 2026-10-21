"""``GiveFeedback``: validation, ownership, replacement of an earlier rating."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from tests.fakes.feedback import FeedbackStore, InMemoryFeedbackUnitOfWork

from scout.feedback.application.give_feedback import GiveFeedback
from scout.feedback.domain.feedback import Rating, Reason, TargetType, feedback_id
from scout.shared import AccountId, FixedClock, NotFound, ValidationFailed

ME = AccountId(UUID(int=1))
OTHER = AccountId(UUID(int=2))
OPP = UUID(int=100)
T0 = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


def make(clock: FixedClock | None = None) -> tuple[GiveFeedback, FeedbackStore, FixedClock]:
    store = FeedbackStore(rateable={(ME, TargetType.OPPORTUNITY, OPP)})
    clk = clock or FixedClock(T0)
    return GiveFeedback(lambda: InMemoryFeedbackUnitOfWork(store), clk), store, clk


async def test_stores_a_rating_with_reasons_and_comment() -> None:
    uc, store, _ = make()
    fb = await uc.execute(
        ME, TargetType.OPPORTUNITY, OPP, Rating.DOWN, [Reason.NOT_FEASIBLE], "  needs a cleanroom "
    )
    assert fb.id == feedback_id(ME, TargetType.OPPORTUNITY, OPP)
    assert fb.comment == "needs a cleanroom"
    assert len(store.ratings) == 1


async def test_duplicate_reasons_collapse_in_order() -> None:
    uc, _, _ = make()
    fb = await uc.execute(
        ME, TargetType.OPPORTUNITY, OPP, Rating.UP, [Reason.SPOT_ON, Reason.TOO_VAGUE, Reason.SPOT_ON]
    )
    assert fb.reasons == (Reason.SPOT_ON, Reason.TOO_VAGUE)


async def test_rating_again_replaces_and_keeps_created_at() -> None:
    uc, store, clk = make()
    first = await uc.execute(ME, TargetType.OPPORTUNITY, OPP, Rating.UP)
    clk.advance(timedelta(hours=1))
    second = await uc.execute(ME, TargetType.OPPORTUNITY, OPP, Rating.DOWN, [Reason.ALREADY_KNEW])
    assert len(store.ratings) == 1
    assert second.rating is Rating.DOWN and second.reasons == (Reason.ALREADY_KNEW,)
    assert second.created_at == first.created_at


async def test_someone_elses_or_unknown_target_is_not_found() -> None:
    uc, store, _ = make()
    with pytest.raises(NotFound):
        await uc.execute(OTHER, TargetType.OPPORTUNITY, OPP, Rating.UP)
    with pytest.raises(NotFound):
        await uc.execute(ME, TargetType.OPPORTUNITY, UUID(int=555), Rating.UP)
    assert store.ratings == {}


async def test_overlong_comment_is_rejected_before_anything_is_written() -> None:
    uc, store, _ = make()
    with pytest.raises(ValidationFailed):
        await uc.execute(ME, TargetType.OPPORTUNITY, OPP, Rating.UP, comment="x" * 1001)
    assert store.ratings == {}


async def test_mine_reads_only_my_own_rating() -> None:
    uc, _, _ = make()
    await uc.execute(ME, TargetType.OPPORTUNITY, OPP, Rating.UP)
    assert (await uc.mine(ME, TargetType.OPPORTUNITY, OPP)) is not None
    assert (await uc.mine(OTHER, TargetType.OPPORTUNITY, OPP)) is None
