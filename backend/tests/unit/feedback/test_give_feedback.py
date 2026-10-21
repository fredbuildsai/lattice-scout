"""``GiveFeedback``: validation, ownership, replacement of an earlier rating."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from tests.fakes.feedback import FeedbackStore, InMemoryFeedbackUnitOfWork, InMemoryTargetCheck

from scout.feedback.application.give_feedback import UNSET, GiveFeedback
from scout.feedback.domain.feedback import Rating, Reason, TargetType, feedback_id
from scout.shared import AccountId, FixedClock, NotFound, ValidationFailed

ME = AccountId(UUID(int=1))
OTHER = AccountId(UUID(int=2))
OPP = UUID(int=100)
T0 = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


def make(clock: FixedClock | None = None) -> tuple[GiveFeedback, FeedbackStore, FixedClock]:
    store = FeedbackStore(rateable={(ME, TargetType.OPPORTUNITY, OPP)})
    clk = clock or FixedClock(T0)
    return (
        GiveFeedback(lambda: InMemoryFeedbackUnitOfWork(store), clk, InMemoryTargetCheck(store.rateable)),
        store,
        clk,
    )


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


async def test_a_rating_without_reasons_or_comment_keeps_the_earlier_ones() -> None:
    """M4 review 3: a thumb click on a list card must not erase what was saved on the detail page."""
    uc, _, _ = make()
    await uc.execute(
        ME, TargetType.OPPORTUNITY, OPP, Rating.DOWN, [Reason.NOT_FEASIBLE, Reason.TOO_VAGUE], "needs a lab"
    )
    flipped = await uc.execute(ME, TargetType.OPPORTUNITY, OPP, Rating.UP)  # reasons=None, comment=UNSET
    assert flipped.rating is Rating.UP
    assert flipped.reasons == (Reason.NOT_FEASIBLE, Reason.TOO_VAGUE) and flipped.comment == "needs a lab"


async def test_explicit_empty_values_clear_reasons_and_comment() -> None:
    uc, _, _ = make()
    await uc.execute(ME, TargetType.OPPORTUNITY, OPP, Rating.DOWN, [Reason.NOT_FEASIBLE], "needs a lab")
    cleared = await uc.execute(ME, TargetType.OPPORTUNITY, OPP, Rating.DOWN, [], None)
    assert cleared.reasons == () and cleared.comment is None
    again = await uc.execute(ME, TargetType.OPPORTUNITY, OPP, Rating.DOWN, None, UNSET)
    assert again.reasons == () and again.comment is None


async def test_reasons_must_fit_the_target_type() -> None:
    uc, store, _ = make()
    with pytest.raises(ValidationFailed):
        await uc.execute(ME, TargetType.OPPORTUNITY, OPP, Rating.UP, [Reason.METHODS_FIT])
    match = UUID(int=300)
    store.rateable.add((ME, TargetType.MATCH, match))
    with pytest.raises(ValidationFailed):
        await uc.execute(ME, TargetType.MATCH, match, None, [Reason.SPOT_ON], stars=4)
    ok = await uc.execute(ME, TargetType.MATCH, match, None, [Reason.METHODS_FIT], stars=4)
    assert ok.reasons == (Reason.METHODS_FIT,)
    assert store.ratings.keys() == {(ME, TargetType.MATCH, match)}


def test_the_domain_enforces_at_most_five_reasons() -> None:
    from scout.feedback.domain.feedback import Feedback

    # the five opportunity chips are exactly the limit; a sixth (a match chip) is refused by the per-target set
    five = [Reason.SPOT_ON, Reason.ALREADY_KNEW, Reason.NOT_FEASIBLE, Reason.WRONG_FIELD, Reason.TOO_VAGUE]
    assert len(Feedback.give(ME, TargetType.OPPORTUNITY, OPP, Rating.UP, five, None, T0).reasons) == 5
    with pytest.raises(ValidationFailed):
        Feedback.give(ME, TargetType.OPPORTUNITY, OPP, Rating.UP, [*five, Reason.METHODS_FIT], None, T0)
