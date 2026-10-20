# Backlog

Ideas and out-of-scope items noticed during the build. Do not implement them in the MVP.

- Reach map and network graph (screens W8, M9)
- Ask Scout chat, voice and photo (W9, M10, M11)
- Funding-call and collaboration opportunity kinds
- Non-member matching (plan P2)
- Embeddings via `EmbeddingPort` if pilot feedback shows weak synergies (ARCHITECTURE §6.6)
- Analytics writer in its own process / thread-free COPY pipeline if `/health` p95 overhead (+1 ms) matters (docs/perf/M0-events-loadtest.md)
- llm-router: upstream `store_text=False` option so its SQLite ledger does not keep vault-derived response text
- Tail sampling of errors in an OTel collector (SDK samples at the head only)
