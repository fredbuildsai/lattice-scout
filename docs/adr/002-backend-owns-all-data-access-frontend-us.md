# ADR 002: Backend owns all data access; frontend uses Supabase only for auth

- **Status:** accepted
- **Date:** 2026-10-20 (from the hand-off architecture)

## Context
See [ARCHITECTURE.md](../ARCHITECTURE.md) §15 and the decision register §0.1.

## Decision
Backend owns all data access; frontend uses Supabase only for auth

## Consequences
One place for privacy rules and anonymity guarantees.
