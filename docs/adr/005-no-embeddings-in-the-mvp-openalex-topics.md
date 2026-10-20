# ADR 005: No embeddings in the MVP; OpenAlex topics + controlled method vocabulary instead. If added later: pgvector in Supabase, not a separate vector DB

- **Status:** accepted
- **Date:** 2026-10-20 (from the hand-off architecture)

## Context
See [ARCHITECTURE.md](../ARCHITECTURE.md) §15 and the decision register §0.1.

## Decision
No embeddings in the MVP; OpenAlex topics + controlled method vocabulary instead. If added later: pgvector in Supabase, not a separate vector DB

## Consequences
Simpler, cheaper, explainable scores; `EmbeddingPort` keeps the door open (§6.6).
