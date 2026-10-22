"""Matching enumerations."""

from __future__ import annotations

from enum import StrEnum


class MatchState(StrEnum):
    """Lifecycle of a proposal. ``mutual``, ``declined`` and ``expired`` are terminal."""

    PROPOSED = "proposed"
    A_ACCEPTED = "a_accepted"
    B_ACCEPTED = "b_accepted"
    MUTUAL = "mutual"
    DECLINED = "declined"
    EXPIRED = "expired"


class Side(StrEnum):
    """Which participant."""

    A = "a"
    B = "b"

    @property
    def other(self) -> Side:
        """The other participant."""
        return Side.B if self is Side.A else Side.A


class Decision(StrEnum):
    """A stored decision."""

    ACCEPT = "accept"
    DECLINE = "decline"


class Action(StrEnum):
    """What a participant asks for."""

    ACCEPT = "accept"
    DECLINE = "decline"
    WITHDRAW = "withdraw"


class ViewerStatus(StrEnum):
    """What one participant is told. Never derived from the other side's decision (except ``mutual``)."""

    AWAITING_YOU = "awaiting_you"
    WAITING_ON_THEM = "waiting_on_them"
    MUTUAL = "mutual"
    DECLINED_BY_YOU = "declined_by_you"
    EXPIRED = "expired"


class CloseReason(StrEnum):
    """Why a proposal was declined."""

    DECLINED = "declined"
    CONSENT_WITHDRAWN = "consent_withdrawn"
    ACCOUNT_DELETED = "account_deleted"


class IdeasStatus(StrEnum):
    """Joint idea generation after the reveal."""

    NONE = "none"
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class Region(StrEnum):
    """Coarse region shown on the anonymous card."""

    AFRICA = "africa"
    ASIA = "asia"
    EUROPE = "europe"
    NORTH_AMERICA = "north_america"
    SOUTH_AMERICA = "south_america"
    OCEANIA = "oceania"
    UNKNOWN = "unknown"


class CareerBand(StrEnum):
    """Coarse career stage shown on the anonymous card."""

    EARLY = "early"
    MID = "mid"
    SENIOR = "senior"
    UNKNOWN = "unknown"
