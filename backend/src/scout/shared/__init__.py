"""Shared kernel: ids, clock, errors, events, unit of work."""

from scout.shared.clock import Clock, FixedClock, SystemClock
from scout.shared.errors import (
    Conflict,
    DomainError,
    Forbidden,
    InfrastructureError,
    InvalidStateTransition,
    InvariantViolation,
    NotFound,
    RateLimited,
    ScoutError,
    Unauthenticated,
    ValidationFailed,
    describe_exception,
)
from scout.shared.events import AggregateRoot, DomainEvent
from scout.shared.ids import AccountId, EntityId, IdGenerator, SequentialIdGenerator, UuidGenerator, parse_id
from scout.shared.uow import UnitOfWork, UnitOfWorkFactory

__all__ = [
    "AccountId",
    "AggregateRoot",
    "Clock",
    "Conflict",
    "DomainError",
    "DomainEvent",
    "EntityId",
    "FixedClock",
    "Forbidden",
    "IdGenerator",
    "InfrastructureError",
    "InvalidStateTransition",
    "InvariantViolation",
    "NotFound",
    "RateLimited",
    "ScoutError",
    "SequentialIdGenerator",
    "SystemClock",
    "Unauthenticated",
    "UnitOfWork",
    "UnitOfWorkFactory",
    "UuidGenerator",
    "ValidationFailed",
    "describe_exception",
    "parse_id",
]
