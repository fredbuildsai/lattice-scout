# ADR 012: First-party analytics and usage metering in Postgres, buffered and asynchronous; OpenTelemetry for traces

- **Status:** accepted
- **Date:** 2026-10-20 (from the hand-off architecture)

## Context
See [ARCHITECTURE.md](../ARCHITECTURE.md) §15 and the decision register §0.1.

## Decision
First-party analytics and usage metering in Postgres, buffered and asynchronous; OpenTelemetry for traces

## Consequences
Full data ownership, per user and session from day one, no added latency; tools (Metabase/PostHog) can be added on top.
