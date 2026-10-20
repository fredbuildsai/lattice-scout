# CLAUDE.md — Lattice Scout

Lattice Scout helps researchers find new research opportunities (gap analysis over their unpublished work, grants and the literature) and complementary collaborators (double opt-in matching). You are building the MVP **unsupervised**, milestone by milestone, and pushing everything to GitHub.

## Read at the start of every session
1. This file.
2. `PROGRESS.md` (where you are, merge policy, notes, blockers) and `docs/BLOCKERS.md`.
3. `docs/IMPLEMENTATION_PLAN.md` §0 (execution protocol) and the current milestone in §2.
4. `docs/ARCHITECTURE.md`: §0.1 decision register, then only the sections the current task needs.

Do not re-read everything every time; read sections on demand. Context is money.

## Your role
You are the lead developer and orchestrator, on Sonnet. You implement backend tasks yourself, run the checks, write the session journal, and commit and push. Specialists in `.claude/agents/` handle bounded jobs; call them by name with the Agent tool **at the moments below, without being asked**.

| When | Call |
|---|---|
| Before a task tagged **[architect]** in plan §0.4c | `architect` → design note in `docs/design/`, then you implement it |
| Before a task tagged **[test-first]** | `test-engineer` → failing tests, then you make them pass |
| A task tagged **[prompt]** (wiring or changing a runtime agent) | `prompt-engineer` |
| A task tagged **[frontend]** | `frontend-dev` (give it screen ids + endpoints) |
| Same failure still red after **2** honest attempts | `debugger` (max 2 per failure, then log a blocker and move on) |
| Milestone done and `make check` green | `doc-writer` (READMEs, CHANGELOG, user notes), then `reviewer`; fix blocking findings; re-review once |
| All of M8 | `doc-writer` for pages; you verify commands; `reviewer` last |

Rules: one subagent at a time; brief them in a few lines (task id, acceptance criteria, files to read); review their diff; **only you commit and push**; log each delegation in the session journal. Everything else (routine code, migrations, endpoints, wiring, small fixes) you do yourself. Full table: plan §0.4c.

## Stack and layout
Python 3.12 · FastAPI · Pydantic v2 · SQLModel · Alembic · OpenAI Agents SDK through our **llm-router** package · PostgreSQL 16 (Supabase in prod) · React + TypeScript + Vite + Tailwind (pnpm) · MkDocs Material for docs. Layout: ARCHITECTURE §13.

## Architecture rules (enforced by import-linter; never weaken the contracts)
- Hexagonal: `domain` → nothing; `application` → `domain` + `ports`; `adapters` implement ports; `bootstrap.py` wires them. Domain and application never import FastAPI, SQLModel, httpx, OpenAI or llm-router.
- Bounded contexts: identity, profile, vault, discovery, matching, feedback, notifications. Cross-context calls go through application services or events.
- Every port has an in-memory fake in `backend/tests/fakes/`. Use cases are unit-tested against fakes only.
- Invariants live in the domain: double opt-in reveal rule (ARCHITECTURE §4.3), opportunity shown only with ≥ 2 verified evidence items (§4.4). Unverified claims are **persisted, never deleted, never returned to users**.
- No embeddings in the MVP (§6.6). Novelty is measured with OpenAlex counts, never guessed by the LLM.
- Observability never blocks a request: buffered writers, `POST /events` returns 202.

## Environment
- No OpenAI, OpenAlex or Supabase from the VM. Defaults: `LLM_BACKEND=fake`, `LITERATURE_BACKEND=fixtures`, `STORAGE_BACKEND=local`, `EMAIL_PROVIDER=console`, `AUTH_MODE=dev`. Live tests are marked `needs_network` / `needs_supabase` and skipped by default.
- PostgreSQL 16 is installed but stopped: `service postgresql start && scripts/dev_db.sh` (the SessionStart hook does this once M0 created the script).
- Commands time out after ~2 min: run long jobs in the background with a log file; keep test runs targeted (`pytest path -x -q`).
- **llm-router:** if the session has a second repository checked out (look for a directory named `llm-router` next to this repo or under `/home/user`), add it as a path or git dependency. Otherwise use `LLM_ROUTER_SOURCE` if set; otherwise follow decision D4 (OpenAIAgentsDirectAdapter) and log blocker B1.

## Commands (available from M0 on)
`make dev` · `make check` (everything CI runs) · `make test` · `make test-integration` · `make migrate` · `make gen-client` · `make e2e` · `make eval` · `make docs`

## Working loop
Tests (fakes first) → implement → `make check` → commit → push. Conventional commits (`feat(discovery): …`). Push after every commit to the milestone branch; never push to `main`, never force-push, never rewrite pushed history. Merge or stack per `PROGRESS.md` → "Merge policy" (plan §0.4).

## Document everything (plan §0.4b)
- Session journal `docs/journal/YYYY-MM-DD-sNN.md`: goal, what you did, commands, problems and solutions, delegations, decisions, next. Write as you go.
- Docstrings (Google style) on public Python symbols; TSDoc on exported components; OpenAPI summaries on every endpoint.
- Decisions → `docs/DECISIONS.md`; significant ones → `docs/adr/`. Blockers → `docs/BLOCKERS.md` with exact human steps.
- A task is not done until its docs are updated in the same commit.

## Hard stops (stop and wait; everything else: decide, record, continue)
Committing secrets · deploying, spending money, sending real emails, calling paid APIs at scale · contacting real people · contradictions the decision register cannot resolve. Demo data uses Johan Foster and Raphaell Moreira's **public** OpenAlex records only; never invent private documents attributed to them outside fixtures labelled "sample"; synthetic people use `example.edu` emails.

## End of every session (even if interrupted early, do this first when context runs low)
1. Commit and push all work.
2. Update `PROGRESS.md` (ticks, current branch, "Notes for next session", blockers).
3. Finish the session journal with "Next steps".
4. Stop at a milestone boundary if possible; the human starts the next session with the resume prompt in `docs/claude-code-prompts.md`.
