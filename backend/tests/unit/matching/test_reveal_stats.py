"""``career_since`` and ``joint_works_count`` exist only on the revealed view (ADR 015/016)."""

from __future__ import annotations

from tests.unit.matching._builders import A_ID, NOW, apply, make_proposal

from scout.api.schemas.matching import MatchListOut
from scout.matching.domain.enums import Action, CareerBand, Region, Side
from scout.matching.domain.reveal import (
    AnonymousMatchView,
    Contact,
    RevealedMatchView,
    build_anonymous,
    build_revealed,
)

STATS = {"career_since", "joint_works_count"}


def _contact(**kw) -> Contact:
    return Contact(
        display_name="Ada Example", institution=None, email="ada@example.edu", orcid=None, openalex_ids=[],
        region=Region.EUROPE, career_band=CareerBand.MID, **kw,
    )  # fmt: skip


def _mutual():
    p = make_proposal()
    apply(p, Side.A, Action.ACCEPT)
    apply(p, Side.B, Action.ACCEPT)
    return p


def test_revealed_view_carries_year_and_joint_count() -> None:
    v = build_revealed(_mutual(), A_ID, _contact(career_since=2013, joint_works_count=5), [])
    assert (v.career_since, v.joint_works_count) == (2013, 5)
    assert v.model_dump(mode="json")["career_since"] == 2013


def test_zero_joint_works_is_omitted_and_missing_year_is_none() -> None:
    v = build_revealed(_mutual(), A_ID, _contact(), [])
    assert v.career_since is None and v.joint_works_count is None


def test_contact_payload_does_not_repeat_the_numbers() -> None:
    v = build_revealed(_mutual(), A_ID, _contact(career_since=2013, joint_works_count=5), [])
    assert not STATS & set(v.model_dump(mode="json")["contact"])


def test_anonymous_view_cannot_carry_them() -> None:
    assert not STATS & set(AnonymousMatchView.model_fields)
    assert STATS <= set(RevealedMatchView.model_fields)
    anon = build_anonymous(make_proposal(), A_ID, NOW, "bio based materials")
    assert not STATS & set(anon.model_dump(mode="json"))
    defs = MatchListOut.model_json_schema()["$defs"]
    assert not STATS & set(defs["AnonymousMatchView"]["properties"])
    assert STATS <= set(defs["RevealedMatchView"]["properties"])
