# ADR 001: Hexagonal architecture with per-context modules

- **Status:** accepted
- **Date:** 2026-10-20 (from the hand-off architecture)

## Context
See [ARCHITECTURE.md](../ARCHITECTURE.md) §15 and the decision register §0.1.

## Decision
Hexagonal architecture with per-context modules

## Consequences
Swap LLM, literature source, storage or queue without touching use cases; test with fakes.
