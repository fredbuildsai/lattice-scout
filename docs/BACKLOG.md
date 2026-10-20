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
- Menu search field "Jump to a page, idea or person…" (W11) and "Analysis runs" entry once `/runs` lists exist (M4)
- Job fencing token (worker id on complete/fail) if long-running jobs appear
- Move event enrichment out of `api/events.py` into an `IngestEvents` service; neutral module for logging config (review M0 #13)
- Per-account daily cap for `/profile/candidates` and per-run OpenAlex budget (M3/M6)
- Refuse `AUTH_MODE=dev` unless explicit `APP_ENV` (review M1 #12): today a loud startup warning is logged

## Fix list (found by running the app, 2026-10-20)

Things that are wrong or unfinished in what already exists, as opposed to the ideas above. Status: `open`, `fixed (commit)`, `wontfix (why)`. IDs are stable: reference them in commits (`fix(frontend): … (F-3)`). Items marked *unverified* were seen in a screenshot but not reproduced.

| ID | Area | Item | Prio | Status |
|---|---|---|---|---|
| F-1 | frontend | Plurals: "1 works" and "1 citations" (`OnboardingPage.tsx` lines ~91 and ~113, `WorksStep.tsx` citations). Add one `plural()` helper and a test. | low | open |
| F-2 | frontend | Nav shows Private vault, Opportunities and Matches although the features do not exist until M2+. Check what each page shows; hide, or show an explicit "coming soon" state. | med | open, *unverified* what the pages render |
| F-3 | frontend | "Ask Scout" floating button is visible on every page but the chat is a backlog item (W9). Check what it does; hide it until it works. | med | open, *unverified* |
| F-4 | frontend | Sticky header appears mid-page in the full-page works screenshot. Probably a capture artefact; confirm in a normal scrolling browser. | low | open, *unverified* |
| F-5 | frontend | Home is a single profile card with an empty right side and no next step. Needs a call to action once M2 exists (upload to the vault). | low | open (M2) |
| F-6 | frontend | Search results list three unaffiliated "Johan Foster" cards whose only difference is the work count. Show a sample work title or a topic so users can tell them apart. | med | open |
| F-7 | product | `contested` verified level is hidden from users, so someone cannot see that another account holds the same OpenAlex record (DECISIONS 36; admin resolution planned for M7). | med | open, needs human decision |
| F-8 | backend | API p95 under 2 000 events/s misses the ±5 % criterion (DECISIONS 24). | low | open, needs human decision |
| F-9 | backend | OpenAlex call budgets per run and per account deferred (DECISIONS 39). Already listed above; tracked here so it is not lost. | med | open (M3/M6) |
| F-10 | tooling | llmrouter-free is installed by hand and `uv sync` removes it (BLOCKERS B3). | med | open, needs human steps |
