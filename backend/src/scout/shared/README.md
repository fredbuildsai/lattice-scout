# shared — kernel

Stdlib + pydantic only. `ids` (UUID ids, `IdGenerator`), `clock` (`Clock`, `SystemClock`, `FixedClock`), `errors` (`ScoutError` tree with problem+json hints), `events` (`DomainEvent`, `AggregateRoot`), `uow` (`UnitOfWork`). Design: `docs/design/M0-3-4-kernel-bootstrap.md`. Tests: `tests/unit/shared/`.
