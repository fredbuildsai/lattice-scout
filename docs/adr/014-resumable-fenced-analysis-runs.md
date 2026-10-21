# ADR-014: Resumable, fenced analysis runs with an exact budget ledger

Status: accepted (M3, 2026-10-21). Decisions 57, 59, 62, 71, 72 and 74.

## Context

A gap analysis run takes minutes, calls paid models and a rate-limited literature API, and runs on workers that can
be killed. Users must never see an unverified claim, and a run must never spend more than its budget.

## Decision

- A run is a state machine of eight ordered steps. Each execution takes a new `generation` (fencing token); every
  write locks the run row and checks it, so a worker that lost its lease cannot overwrite a newer execution.
- Steps persist their work with deterministic ids and skip what exists, so a retry resumes at `run.step` and repeats
  nothing that completed. `complete_step` is its own transaction (a kill before it repeats one idempotent pass).
- Spend is kept in the run row by a ledger that charges after each call in its own transaction. Model calls reserve
  their maximum cost while in flight; an invalid answer is charged at the reserve. OpenAlex calls are counted
  before they are made, capped at 400.
- An opportunity is shown only when at least two distinct verified evidence items (one of them literature) support
  verified claims and a measured novelty count exists. The rule is evaluated in Python on every read.

## Consequences

- Retries are cheap and safe; a lost lease costs at most one call of unrecorded spend (BACKLOG F-32).
- Visibility needs no cleanup job when a source document is deleted.
- A cancel can be lost during the last step (BACKLOG F-26).
