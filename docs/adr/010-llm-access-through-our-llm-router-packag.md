# ADR 010: LLM access through our llm-router package (no LiteLLM)

- **Status:** accepted
- **Date:** 2026-10-20 (from the hand-off architecture)

## Context
See [ARCHITECTURE.md](../ARCHITECTURE.md) §15 and the decision register §0.1.

## Decision
LLM access through our llm-router package (no LiteLLM)

## Consequences
Reuse in-house routing and fallbacks; single place to meter tokens and cost. Integration with the Agents SDK to confirm at M0.
