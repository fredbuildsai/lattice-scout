# discovery — bounded context

**Purpose:** the gap-analysis engine. A run reads the researcher's claimed profile and verified vault material, searches the literature, groups the results into topic clusters, measures how many papers already cover each candidate gap, and asks agents for opportunities. Code checks every citation, a Critic reviews what passes, and only opportunities with enough verified evidence are shown (ARCHITECTURE §3.2, §4.2 to §4.4).
**Key aggregates:** `AnalysisRun` (status, step, generation, budget spend), `Opportunity` (with its `Claim`s and `Evidence`).
**Design and decisions:** `docs/design/M3-gap-analysis-engine.md`; `docs/DECISIONS.md` 56 to 73.

**Status:** M3 backend implemented: the eight-step pipeline, the `discovery.run` and `discovery.sweep` jobs, budgets and fencing, the read side, the run and opportunity endpoints, the admin claims view, migrations `0005` and `0006`, the offline eval (`make eval`) and the fixtures. Not yet: the opportunity screens (M4). Differences from the design, recorded in DECISIONS 73 and in the code: visibility is decided in Python on every read, so the SQL view `discovery_visible_opportunity` from design §7 does not exist; the analytics catalogue has no `run_cancelled` event yet.

**Layout** (same for every context):

| Folder | Holds |
|---|---|
| `domain/` | Entities, value objects, invariants and pure functions. Stdlib and Pydantic only. |
| `application/` | Use cases, the pipeline steps and the run guard. Imports `domain` and `ports` only. |
| `ports/` | Repositories and unit of work, the run ledger, the agents port, and the profile and vault sources. |
| `adapters/` | SQL repositories and their serialisation, the agents adapter, the field config loader. |

Outside this folder: `scout/shared/text.py` (`norm`, `clean_text`), the shared literature port (`scout/ports/literature.py`), the LLM port (`scout/ports/llm.py`), `scout/api/runs.py`, `scout/api/opportunities.py`, `scout/api/admin.py`, `scout/api/schemas/discovery.py`, `config/fields.yaml`, `prompts/` (QueryPlanner, ClusterLabeler, InsightTopicTagger, GapAnalyst, Critic), `backend/migrations/versions/20261021_0005_discovery.py` and `20261021_0006_discovery_views.py`.

The import rules are enforced by import-linter (`make contracts`). Discovery domain and application never import `scout.vault.*`, `scout.profile.*` or `scout.identity.*`. The only module that does is `application/sources.py`, which maps the vault and profile services to discovery DTOs (DECISIONS 68).

## Domain model

| Module | Contents |
|---|---|
| `domain/ids.py` | `RunId`, `OpportunityId`, `ClaimId`, `EvidenceId`, `ClusterId`, `CorpusWorkId`, `ProbeId`. Ids inside a run are `uuid5` (`corpus_work_id`, `cluster_id`, `probe_id`, `opportunity_id`, `claim_id`, `evidence_id`), so a repeated step writes the same rows. |
| `domain/enums.py` | `RunStatus`, `RunStep` (eight steps in order), `StepStatus`, `OppKind`, `OppStatus`, `ClaimStatus`, `ClaimSection`, `SourceType`, `SetBy`, `CorpusOrigin`, `CheckCode`, `RunFailure`. |
| `domain/run.py` | `AnalysisRun` aggregate: `begin_execution`, `complete_step`, `record_progress`, `degrade`, `fail`, `request_cancel`, `cancel`, the derived `phase` and `percent()`. Also `RunInputs`, `StepRecord`, `Degradation`, `Superseded`, `RunCancelled`, `RunFailed`. |
| `domain/budget.py` | `RunBudget`, `RunSpend`, `can_start_llm`, `can_call_openalex`, `BudgetExhausted`, `OpenAlexCapReached`. |
| `domain/opportunity.py` | `Opportunity` (`show_decision`, `visible_claims`, `set_user_status`), `Claim`, `Evidence` (`mark_verified` checks that a reference resolves inside the run), `Scores`. |
| `domain/verification.py` | `check_ref` and `check_claim` (one `CheckCode` per rule), `claim_passes`, `RunMaterialIndex` and its materials. |
| `domain/grouping.py` | `group_works`: primary topic clusters, merge small groups by subfield, split large ones by secondary topic. Every work lands in exactly one cluster. |
| `domain/coverage.py` | `user_coverage`, `density`, `candidate_scores`, `pick_candidates` (at most 8). |
| `domain/novelty.py` | `novelty_score` (monotone non-increasing in the count), `probe_queries`. |
| `domain/scoring.py` | `fit_score`, `readiness_score`, `effort_value`, `rank_score`, `title_tokens`, `jaccard`, `is_duplicate` (threshold 0.6). |
| `domain/agent_io.py` | Pydantic models of every agent input and output (`QueryPlan`, `ClusterLabel`, `GapAnalysis`, `CriticVerdict` and the others). Every string is cleaned and bounded. |
| `domain/field.py`, `domain/corpus.py` | `FieldConfig` (from `config/fields.yaml`), `CorpusWork`, `Cluster`, `NoveltyProbe`. |

## Ports

| Port | Module | Purpose |
|---|---|---|
| `RunRepo`, `CorpusRepo`, `ClusterRepo`, `ProbeRepo`, `OpportunityRepo`, `DiscoveryUnitOfWork` | `ports/repos.py` | Persistence. Every step commits in one unit of work. `RunInProgress` is raised when the owner already has an active run. |
| `RunLedgerPort` | `ports/ledger.py` | `charge(run_id, generation, ...)` returns `LedgerState {spend, cancel_requested, superseded}`. |
| `DiscoveryAgentsPort` | `ports/agents.py` | `plan_queries`, `label_cluster`, `tag_insights`, `analyse`, `critique`. Each returns an `AgentOutcome` (output, prompt version, model, cost, tokens). |
| `ProfileSource`, `VaultSource` | `ports/sources.py` | Read-only views for a run: the profile (works, topics) and the verified vault material (`run_material`, `passage`). |
| `LiteraturePort` (shared) | `scout/ports/literature.py` | `search_works`, `count_works`, `get_work`. Always wrapped by `BudgetedLiterature`. |

In-memory fakes are in `backend/tests/fakes/discovery.py`: the repositories and unit of work (`InMemoryDiscoveryUoW`), `InMemoryLedger`, `FakeProfileSource`, `FakeVaultSource` and `FakeDiscoveryAgents`.

## Application

| Module | Contents |
|---|---|
| `application/start.py` | `StartAnalysisRun.start`: input checks, start rules, the run row and the `discovery.run` job. |
| `application/run_analysis.py` | `RunAnalysis.handle` (the `discovery.run` job) and `SweepRuns` (the `discovery.sweep` job). |
| `application/steps_retrieval.py` | Steps 1 to 4: `plan_queries`, `retrieve`, `group_by_topic`, `coverage`. |
| `application/steps_analysis.py` | Steps 5 to 8: `measure_novelty`, `analyse`, `verify`, `score_persist`. |
| `application/pipeline.py` | `PipelineConfig` (the tunables of one run) and `StepContext` (one per execution). |
| `application/guard.py` | `RunGuard` (budget reservations, one per execution) and `BudgetedLiterature` (counts every OpenAlex call). |
| `application/service.py` | `DiscoveryService`: the read side and the user commands (cancel, status changes, evidence, admin claims). |
| `application/sources.py` | `ProfileSourceAdapter` and `VaultSourceAdapter`. The only place that touches other contexts. |

## Pipeline

A run has eight steps, in this order. Each step commits its own checkpoints, so a restart continues at the last one.

| # | Step (`RunStep`) | What it does | Agent or port calls | Degrades when |
|---|---|---|---|---|
| 1 | `plan_queries` | Builds at most 25 queries from the profile, verified insight summaries and keywords. The code sets the date window (`from_publication_date`, 6 years back) and `type=article`. Profile works go into the corpus. | QueryPlanner ×1 | Budget hit: the run fails `budget_exhausted`, since nothing exists yet. |
| 2 | `retrieve` | Runs each query in waves and stores the works (dedupe by OpenAlex id, then DOI, at most `RUN_MAX_CORPUS` works). | `search_works` per query | The OpenAlex cap is hit: `openalex_cap`, and the corpus so far is kept. No search works at all: the run fails `corpus_empty`. |
| 3 | `group_by_topic` | Groups works into clusters (at most 30 labelled). Small clusters merge by subfield. Labels are `k1..`. | ClusterLabeler | Fallback label (topic or subfield name): `label_fallback`. |
| 4 | `coverage` | Counts the researcher's works and verified insights per cluster, and picks the candidate clusters (at most 8). | InsightTopicTagger, in batches | No tags: `tags_skipped` (works-only coverage). |
| 5 | `measure_novelty` | For each candidate, counts matching papers since 2021 with OpenAlex (`count_works`, at most 2 probes per candidate). Probes are `n1..` and stored. | none (OpenAlex only) | Candidates without any count are dropped: `openalex_cap`. |
| 6 | `analyse` | Asks GapAnalyst for up to 5 opportunities per batch of 4 candidates, with at most 2 batches. Drafts are stored as `draft` with `pending` claims. | GapAnalyst | Remaining batches skipped: `analyse_truncated`. |
| 7 | `verify` | Checks every citation in code (`check_ref`, `check_claim`). Missing works are resolved with `get_work` (at most 20 per run). Drafts that pass go to the Critic. | `get_work`, Critic ×1 per draft | Remaining drafts: `budget_exhausted`. The last attempt marks them `critic_unavailable`. |
| 8 | `score_persist` | Computes scores, adds the novelty `score` claim, dedupes titles, applies the show rule, and sets `shown` or `unverified`. Then the run is `done`. | none | n/a |

The run summary counts (`corpus`, `clusters`, `candidates`, `drafts`, `shown`) are in `GET /api/v1/runs/{id}`.

## Show rule (DECISIONS 59)

`Opportunity.show_decision()` returns `(True, "ok")` only when all of these hold:

1. At least two distinct `(source_type, ref)` items are verified evidence on verified `rationale` or `objection` claims.
2. At least one of those items is `literature`. Profile works are not literature.
3. A verified `score` claim exists, with verified `count` evidence, and `scores.novelty` is set. This is the measured novelty count.

Otherwise the reason is `insufficient_evidence`, `no_literature` or `no_novelty_measure`. The rule is checked again on every read (DECISIONS 73), so an opportunity that loses evidence (for example, a deleted document) disappears from lists, detail and evidence views without a cleanup job.

Other rules that keep an opportunity off the screen:

- **Critic.** An opportunity the Critic rejects, or that it marks `already_done`, becomes `rejected_by_critic`. Its verified claims become `rejected_by_critic`. The Critic's own verdict stays in `critic_verdict`.
- **Duplicates.** Title token Jaccard of at least 0.6 against the owner's earlier `shown`, `saved` or `dismissed` opportunities, or a higher-ranked one in the same run, gives `duplicate` with `duplicate_of`. Dismissed ideas therefore do not come back.
- **Unverified.** An opportunity with no claim that passes the code checks is `unverified` (`no_verified_claims`), and the Critic is not called.

## Claims, evidence and checks (DECISIONS 60, 72)

- Every claim has a status, a reason (`CheckCode` or a Critic reason), the step that set it (`set_by`), the agent, the prompt version and the model.
- Evidence is literature or profile (OpenAlex id), vault (chunk id, with a verbatim quote of at most 300 characters or an insight reference) or count (novelty probe id).
- Code checks (`check_ref`): a work must be in the corpus (or resolved); a quote must be at least 15 and at most 300 characters after `norm`, and found in the abstract or the passage; a chunk must be in the run's index and its document active; an insight must be a verified insight of the run; a count must come from a measured probe.
- Claim checks (`check_claim`): no citation gives `no_evidence`; an e-mail address gives `personal_data`; novelty wording without a count gives `novelty_without_count`; an integer of 10 or more that does not match the measured count within ±5 % gives `count_mismatch`.
- Unverified rows are **stored, never deleted, and never returned to users**. `visible_claims()` returns only verified claims with verified evidence.

## Budgets, ledger and fencing (DECISIONS 57, 62)

- **Ledger.** `RunLedgerPort.charge` adds to `analysis_run` in one short `UPDATE ... RETURNING`. It runs after every LLM call (cost, tokens, one call) and before every OpenAlex call. The totals are exact across restarts. `llm_call` and `external_call` rows carry `run_id` for analytics only.
- **Reservations.** `RunGuard.llm(tier)` refuses a call unless `spend + in-flight reservations + reserve(tier) <= budget`. Reserves are USD 0.02 (fast) and 0.30 (reasoning). The overshoot is bounded by one in-flight call.
- **OpenAlex.** `BudgetedLiterature` counts every port call, cache hits included, against `RUN_MAX_OPENALEX_CALLS` (at most 400). A run uses at most 25 + 2 × 8 + 20 = 61 port calls in the worst case.
- **Account limits.** At start: at most `RUN_MAX_PER_DAY` runs in 24 hours, and spend in 24 hours below `ACCOUNT_DAILY_BUDGET_USD`. The run budget is `min(RUN_BUDGET_USD, remaining daily budget)`.
- **Fencing.** `begin_execution` increments `generation`. Every step commit locks the run row and checks `generation` and `step`. A worker that lost its lease gets `Superseded` at its next call or commit and stops (`{"skipped": "superseded"}`).
- **Cancel.** `POST /api/v1/runs/{id}/cancel` on a queued run cancels it at once. On a running run it sets `cancel_requested_at`, and the run stops at the next checkpoint or ledger charge. Nothing from a cancelled run is shown, because opportunities only become `shown` in `score_persist`.

## Resume and recovery (DECISIONS 57, 71)

- Kill the worker at any point and restart it. The job is retried (`max_attempts=5`). The new execution begins at `run.step` and skips work already persisted: done query labels, measured probes, labelled clusters, finished batches and drafts.
- A `DomainError` (for example `RunFailed(code)`) is permanent: the run fails with that code. Any other exception is transient and the job is retried. On the last attempt the run fails with `llm_unavailable`, `literature_unavailable` or `internal_error`. Exception: in `verify`, the last attempt does not fail the run. The remaining drafts get `critic_unavailable` and the run ends `partial`.
- `discovery.sweep` runs every 10 minutes, scheduled at worker start. It re-enqueues `queued` runs older than 5 minutes and `running` runs whose `heartbeat_at` is older than `RUN_STALL_MINUTES`. After `RUN_MAX_EXECUTIONS` executions it fails the run with `stalled` instead. At most 100 runs per sweep.
- A step's outputs and its `complete_step` are separate transactions. A kill between them repeats one cheap, idempotent pass (DECISIONS 71).

## Read side and API

`DiscoveryService` (`application/service.py`) loads aggregates, re-runs `show_decision` on every read and returns views. Endpoints are under `/api/v1`, behind `current_account`, with problem+json errors.

| Endpoint | Purpose |
|---|---|
| `POST /runs` | Start a run (202, `Location` header). Body `document_ids`, 0 to 10 ready documents. |
| `GET /runs`, `GET /runs/{id}` | Latest 20 runs, or one run with phase, steps, percent, `partial`, `degraded`, counts and cost. |
| `POST /runs/{id}/cancel` | Cancel (202). 409 when the run has ended. |
| `GET /opportunities` | Your opportunities. Filters: `run_id`, `status` (`shown`, `saved`, `dismissed`), `kind`, cursor, limit up to 50. |
| `GET /opportunities/{id}` | Detail: verified claims with their citations, next steps, readiness, score basis. |
| `PATCH /opportunities/{id}` | Set `shown`, `saved` or `dismissed`, in any order. |
| `GET /evidence/{id}/open` | The source behind a citation (see below). |
| `GET /admin/claims` | Claims of every status, for the admin role only (403 otherwise). Each request is audited (`admin.claims_viewed`). |

Opening evidence (`EvidenceOpen`): for a vault passage under `keep`, the passage with its pages, read live from the document. Under `discard`, only the retained quote is returned, and `original_available` is false. A deleted document answers 404. Literature and profile evidence return the title, year, DOI, abstract and quote; count evidence returns the query, the count and the start year (no URL yet, BACKLOG F-29).

Admin claims redact vault quotes unless `ADMIN_CLAIMS_SHOW_VAULT_QUOTES=true` (DECISIONS 70, Q2).

## Persistence

| Migration | Creates |
|---|---|
| `20261021_0005_discovery` | `analysis_run` (with the partial unique index `analysis_run_one_active_idx` on `owner_id` where `queued` or `running`), `run_document`, `corpus_work`, `cluster`, `novelty_probe`, `opportunity`, `claim`, `evidence`. |
| `20261021_0006_discovery_views` | Analytics views `analytics.v_claim_quality` and `analytics.v_run_cost`. |

`evidence.quote` is at most 300 characters. Its `chunk_id` cascades when the vault chunk is deleted, so quotes from a deleted document are removed in the same commit. Claims and opportunities stay (DECISIONS 69).

## Events, logs and audit

Logs carry ids, counts and codes only, never vault text.

| Log | Fields |
|---|---|
| `discovery.run_failed` (warning) | `run_id`, `reason` |
| `discovery.enqueue_failed` (error) | `run_id`. The sweeper recovers the run. |
| `discovery.sweep` | counts of queued and stalled runs and failures |
| `discovery.foreign_chunk` (error) | `run_id`. A chunk of another owner was cited; the check refuses it. |

Audit rows: `admin.claims_viewed` (filters). Analytics events for the frontend: `run_started`, `run_viewed`, `opportunity_opened`, `evidence_marker_opened`, `evidence_source_clicked`, `opportunity_saved`, `opportunity_dismissed` (`scout/ports/analytics_catalogue.py`).

## Settings

All names are environment variables read by `backend/src/scout/settings.py`. Defaults are shown as the code has them. The full list with descriptions is in [configuration](https://github.com/fredbuildsai/lattice-scout/blob/main/docs-site/pages/dev/configuration.md).

| Setting | Default | Used by |
|---|---|---|
| `RUN_BUDGET_USD` | `1.50` | Budget of one run (`min` with the daily remainder). |
| `RUN_MAX_OPENALEX_CALLS` | `400` | `RunBudget.openalex_calls`. |
| `RUN_MAX_CORPUS` | `2500` | Corpus size limit in step 2. |
| `RUN_MAX_PER_DAY` | `5` | Runs per account in 24 hours (`StartAnalysisRun`). |
| `ACCOUNT_DAILY_BUDGET_USD` | `5.00` | Spend per account in 24 hours. |
| `RUN_LLM_CONCURRENCY` | `3` | Agent calls at the same time in a step. |
| `RUN_OPENALEX_CONCURRENCY` | `4` | Literature calls at the same time in a step. |
| `RUN_ANALYSE_MAX_CALLS` | `2` | GapAnalyst batches per run. |
| `RUN_MAX_RESOLVE` | `20` | `get_work` resolutions per run. |
| `RUN_STALL_MINUTES` | `20` | Heartbeat age before the sweeper treats a run as stalled. |
| `RUN_MAX_EXECUTIONS` | `8` | Executions before a run fails `stalled`. |
| `FIELDS_CONFIG` | `config/fields.yaml` | Field definitions (topics, keywords, query window, novelty bands, cluster sizes). |
| `LAUNCH_FIELD` | `bio-based-materials` | Field used by the run. |
| `ADMIN_CLAIMS_SHOW_VAULT_QUOTES` | `false` | Whether `/admin/claims` shows vault quotes. |

Constants in code (not settings): reserves USD 0.02 and 0.30 (`domain/budget.py`), at most 8 candidate clusters (`domain/coverage.py`), at most 10 documents per run (`api/schemas/discovery.py`), duplicate threshold 0.6 (`domain/scoring.py`), novelty saturation 5000 and the 5-year window (`fields.yaml`).

## Scoring (DECISIONS 65)

- `fit = 0.6 × mean user topic weight + 0.4 × min(1, insight weight / 2)`.
- `novelty = clamp(1 − log10(count + 1) / log10(saturation + 1), 0, 1)`, with saturation 5000. The band label goes into `score_basis`.
- `readiness` = checklist items that are in the vault and have a resolvable reference, divided by all items (0 when there are none).
- `effort`: low 0.25, medium 0.5, high 0.85, unknown 0.5.
- `rank = 0.35 fit + 0.30 novelty + 0.20 readiness + 0.15 (1 − effort)`.

Recorded counts from the sample fixtures (427 to 1731) give novelty 0.12 to 0.29 with this saturation. The bands need domain review (BACKLOG F-22).

## Invariants

These are domain rules, and the tests check them.

1. Run transitions follow the table in `domain/run.py`. `done`, `failed` and `cancelled` are final. The generation only increases, and `complete_step` needs the current step and generation.
2. Opportunity user statuses move only among `shown`, `saved` and `dismissed`.
3. `visible_claims()` returns only verified claims with verified evidence. Nothing else reaches a read model.
4. Evidence resolves inside its run: a work must be in the corpus, a chunk must belong to a run document of the same owner, and a count must come from the run's probes (otherwise `InvariantViolation`).
5. Discovery code never deletes claims, evidence or opportunities. The only deletions are cascades from a vault deletion (evidence) or from account deletion.
6. Every score is in [0, 1].
7. The show rule holds on every read, and the outcome matches `show_decision`.

## Tests

| Module | Covers |
|---|---|
| `tests/unit/discovery/test_run.py` | Run transitions, generation, cancel, percent. |
| `tests/unit/discovery/test_opportunity.py` | Show rule, visible claims, user status changes. |
| `tests/unit/discovery/test_verification.py` | `check_ref` and `check_claim`, one case per rule. |
| `tests/unit/discovery/test_budget.py`, `test_guard.py` | Reservations, `can_start_llm`, the OpenAlex cap. |
| `tests/unit/discovery/test_pipeline_start.py` | Start rules: ownership, not ready, active run, daily limits. |
| `tests/unit/discovery/test_pipeline.py`, `test_pipeline_budget.py` | Steps on the happy path and budget degradation. |
| `tests/unit/discovery/test_pipeline_resume.py` | Resume after a kill at each step and mid-step, one call per completed unit, fencing (a superseded execution stops). |
| `tests/unit/discovery/test_pipeline_safety.py` | Retries and the last attempt, Critic verdict mapping, discard quotes, hostile drafts, the leak canary. |
| `tests/unit/discovery/test_agent_io.py`, `test_field.py`, `test_ids.py`, `test_helpers.py` | Agent input and output models, field config, ids, grouping, scoring helpers. |
| `tests/integration/test_discovery_pipeline_pg.py` | The pipeline on PostgreSQL: done run, rerun adds no rows, ledger survives, views have rows, resume after a kill during `analyse`. |
| `tests/integration/test_discovery_fixtures.py` | Each eval case runs with the fixtures, and every shown opportunity passes the show rule. |
| `tests/integration/test_discovery_api.py` | Owner isolation, status, evidence, admin role, limits. |

## Commands

```bash
cd backend && uv run pytest tests/unit/discovery -q                     # no database
make test-integration                                                    # needs PostgreSQL (make db)
make eval                                                                # offline eval of the eval cases
cd backend && uv run python ../scripts/make_discovery_fixtures.py --check  # fixtures match the code
cd backend && uv run python ../scripts/make_discovery_fixtures.py          # regenerate the fixtures
```

### How the eval works

`make eval` runs `eval/run_eval.py`. For each case in `eval/cases/*.yaml` (Johan Foster with three fictional sample documents, and Raphaell Moreira profile-only) it runs the real pipeline in memory with `FixtureLiterature` and `FakeLLM`. It then computes metrics and writes `eval/reports/<timestamp>.json`, and prints a Markdown table.

It exits with status 1 when a case shows fewer than 3 or more than 10 opportunities, or when the verified-evidence rate (verified items over all items) is below 0.6. It needs no network. A live mode is not built (BACKLOG F-28). The live run (`--live`) is not run in CI.

Metrics: shown count, verified-evidence rate, Critic rejection rate, `already_done_rate`, `duplicate_of_previous_rate`, theme recall (a theme is recalled when one shown opportunity contains all its keywords), cost per run, wall time.

### How the fixtures are made

The fake LLM answers each agent from `backend/tests/fixtures/llm/<Agent>/<input hash>.json`. The hash comes from the exact input the code sends, so the fixtures change when an input changes. The script `scripts/make_discovery_fixtures.py` runs each eval case with the real application and records each agent's input and output. It writes the output file and the input (`<hash>.input.json`). `--check` exits with status 1 when a fixture is missing, differs or is stale, and also when a case does not meet its bounds.

The fixtures are keyed by the date: they were recorded with the clock at 2026-10-21 (`year_from` 2020, novelty since 2021). A run on a later date can fall back to `default.json`, so its results will not match the fixtures. Rerun the script after changing an eval case, a sample document, an OpenAlex fixture, an InsightExtractor fixture, `agent_io.py` or any code that builds an agent input.

Field config (`config/fields.yaml`) and the novelty bands are reviewed by a domain expert in M7 (BACKLOG F-22).

## Related

- Developer page: [gap analysis](https://github.com/fredbuildsai/lattice-scout/blob/main/docs-site/pages/dev/discovery.md) (local run, endpoints, fixtures).
- Vault module, which supplies verified material: `backend/src/scout/vault/README.md`.

## Confidential data and provenance (ADR 016)

- `StepContext.material()` is empty when no authorized endpoint exists; the run is stored with `basis = published_only`.
- `StepContext.tainted(run)` is true for a run started on a `full` basis with documents. Tainted runs build `GapContext` and `CriticInput` with `tainted=True`: their `derived_field`s (`exclude_titles`, the Critic's `draft`) are confidential, so they go only to authorized deployments or the call is not made (the Critic then leaves the opportunity `unverified`, `critic_unavailable`).
- No vault-derived string goes into an OpenAlex query: the QueryPlanner gets no insights or keywords, and novelty probes use the cluster's search terms only.
- `previous_titles_by_basis` tells an untainted run which earlier titles came from published-only runs (the only ones it may send).
- Regression: `tests/unit/discovery/test_confidential_provenance.py` (spy backend with some tiers authorized).
