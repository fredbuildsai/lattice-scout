# ADR 006: Postgres-backed job queue

- **Status:** accepted
- **Date:** 2026-10-20 (from the hand-off architecture)

## Context
See [ARCHITECTURE.md](../ARCHITECTURE.md) §15 and the decision register §0.1.

## Decision
Postgres-backed job queue

## Consequences
No Redis; adequate throughput; swappable via `JobQueuePort`.
