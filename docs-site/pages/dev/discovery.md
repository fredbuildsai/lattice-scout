# Gap analysis: runs, opportunities and evidence

A **run** turns a researcher's claimed profile and verified vault material into ranked opportunities. Each opportunity is a set of claims, and each claim cites evidence: literature, the researcher's own works, a passage of a vault document, or a count of matching papers. Code checks every citation before a Critic agent reviews the claims. Only opportunities with enough verified evidence are shown.

Code and rules: [discovery module](https://github.com/fredbuildsai/lattice-scout/blob/main/backend/src/scout/discovery/README.md). Design: `docs/design/M3-gap-analysis-engine.md`. Decisions: `docs/DECISIONS.md` 56 to 73. Status: the backend is M3. The opportunity screens are M4, so there is no frontend for runs yet.

## Architecture

The context follows the hexagonal layout (see [developer guide](index.md)):

| Layer | Discovery code | Notes |
|---|---|---|
| Domain | `backend/src/scout/discovery/domain/` | Run state machine, budgets, show rule, checks, grouping, novelty, scores. Stdlib and Pydantic only. |
| Ports | `backend/src/scout/discovery/ports/` | Repositories and unit of work, `RunLedgerPort`, `DiscoveryAgentsPort`, `ProfileSource`, `VaultSource`. |
| Application | `backend/src/scout/discovery/application/` | `StartAnalysisRun`, `RunAnalysis` and `SweepRuns`, the eight steps, `RunGuard`, `DiscoveryService`. |
| Adapters | `backend/src/scout/discovery/adapters/` | SQL repositories, the agents adapter (`AgentsDiscovery`), the field loader. |
| HTTP | `backend/src/scout/api/runs.py`, `opportunities.py`, `admin.py`, `schemas/discovery.py` | Under `/api/v1`, all behind `current_account`. |

`bootstrap.py` wires the adapters. The worker runs two jobs: `discovery.run` and `discovery.sweep`. The sweep is scheduled when the worker starts.

## Run a gap analysis locally with the fixtures

The defaults need no network: `LLM_BACKEND=fake` (answers come from `backend/tests/fixtures/llm/`) and `LITERATURE_BACKEND=fixtures` (works come from `backend/tests/fixtures/openalex/`). The examples use the demo persona, Johan Foster, through his public OpenAlex records only.

1. Start PostgreSQL, apply migrations, then run the API and the worker in two shells:

   ```bash
   make db
   make migrate
   make api        # shell 1
   make worker     # shell 2
   ```

2. Sign in with the development login. It accepts any e-mail address, so use a synthetic one:

   ```bash
   curl -s -X POST http://localhost:8000/api/v1/dev/login \
     -H 'Content-Type: application/json' -d '{"email": "researcher@example.edu"}'
   ```

   The response has `access_token`. Send it as `Authorization: Bearer <token>` in the calls below.

3. Claim the profile. The records are the two public records of the eval case `eval/cases/foster.yaml`. The worker imports the works:

   ```bash
   curl -s -X POST http://localhost:8000/api/v1/profile/claim \
     -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"author_ids": ["A5059588273", "A5140167166"], "primary_author_id": "A5059588273"}'
   ```

   Check the import with `GET /api/v1/profile`. A run needs a finished import, otherwise it answers 409 `profile_required`.

4. Build the three fictional sample documents and upload them. Upload the PDFs under their own file names, because the InsightExtractor fixtures are keyed by file name:

   ```bash
   cd backend && uv run python ../scripts/make_sample_docs.py   # writes tests/fixtures/vault/build/
   curl -s -X POST http://localhost:8000/api/v1/documents \
     -H "Authorization: Bearer $TOKEN" \
     -F 'file=@tests/fixtures/vault/build/declined-proposal-lignin-foams.pdf' \
     -F 'keep_original=true'
   ```

   Repeat for `unpublished-results-cnf-membrane.pdf` and `lab-notes-pfas-membrane.pdf` (with `keep_original=false` for the last two, as in the eval case). Wait until `GET /api/v1/documents` shows `ready` for each one.

5. Start the run with the ids of the ready documents. The response is `202` with a `Location` header:

   ```bash
   curl -s -X POST http://localhost:8000/api/v1/runs \
     -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"document_ids": ["<id-1>", "<id-2>", "<id-3>"]}'
   ```

   Poll `GET /api/v1/runs/{run_id}` every 3 seconds until `status` is `done`, `failed` or `cancelled`. `phase` shows the stage, `percent` the progress, and `partial` and `degraded` whether a budget or a cap cut work short.

6. Read the result:

   ```bash
   curl -s "http://localhost:8000/api/v1/opportunities?run_id=<run-id>" -H "Authorization: Bearer $TOKEN"
   curl -s "http://localhost:8000/api/v1/opportunities/<opportunity-id>" -H "Authorization: Bearer $TOKEN"
   curl -s "http://localhost:8000/api/v1/evidence/<evidence-id>/open" -H "Authorization: Bearer $TOKEN"
   ```

   The default list shows `shown` and `saved` opportunities, best rank first. Add `status=dismissed` to see dismissed ones.

If the fixtures do not match (see below), the fake LLM uses `default.json` and the run may show fewer opportunities.

## Endpoints

All paths are under `/api/v1`. Errors use problem+json.

| Method and path | Success | Errors |
|---|---|---|
| `POST /runs` `{document_ids}` (0 to 10 ready documents) | 202, `Location` | 404 (a document is not yours), 409 `run_in_progress` or `profile_required`, 422 `document_not_ready` or `validation_failed`, 429 `run_limit_reached` or `daily_budget_reached` |
| `GET /runs` | 200, latest 20 runs | |
| `GET /runs/{id}` | 200, one run | 404 |
| `POST /runs/{id}/cancel` | 202 | 404, 409 when the run has ended |
| `GET /opportunities` `?run_id&status&kind&cursor&limit` | 200 `{items, next_cursor}`; `limit` up to 50 | 404 (run not yours), 422 |
| `GET /opportunities/{id}` | 200, detail with verified claims and citations | 404 (not yours, or not visible) |
| `PATCH /opportunities/{id}` `{status}` (`shown`, `saved`, `dismissed`) | 200, card | 404, 409, 422 |
| `GET /evidence/{id}/open` | 200, the source | 404 (not yours, not visible, or the source is gone) |
| `GET /admin/claims` `?status&reason&agent&prompt_version&model&run_id&cursor&limit` | 200 `{items, next_cursor}` | 403 unless the account has the `admin` role |

Opening evidence returns a passage for a document kept with `keep_original=true`, and only the retained quote for a `discard` document. Literature and profile evidence return a link, and count evidence returns the OpenAlex search for the query.

`GET /admin/claims` is for the review team. It hides vault quotes unless `ADMIN_CLAIMS_SHOW_VAULT_QUOTES=true`, and each call writes an `admin.claims_viewed` audit row. To give a local account the admin role, update the row in the `account` table (`UPDATE account SET role = 'admin' WHERE email = 'researcher@example.edu';`).

The OpenAPI summaries of these endpoints are in the generated schema (`make gen-client` regenerates the frontend client from it).

## Run states and steps

A run has the statuses `queued`, `running`, `done`, `failed` and `cancelled`. Its steps are `plan_queries`, `retrieve`, `group_by_topic`, `coverage`, `measure_novelty`, `analyse`, `verify` and `score_persist`. The step table and the show rule are in the [module README](https://github.com/fredbuildsai/lattice-scout/blob/main/backend/src/scout/discovery/README.md#pipeline).

A run that cannot finish a step with its budget still ends `done`, with `partial: true` and a `degraded` list. A failure code is in `error_code`. The codes are `budget_exhausted`, `corpus_empty`, `profile_missing`, `llm_unavailable`, `literature_unavailable`, `stalled` and `internal_error`.

## Regenerate the fixtures

The fake LLM answers each agent from a file named after a hash of the agent's input. The fixture script runs the eval cases with the real application and writes the answers:

```bash
cd backend && uv run python ../scripts/make_discovery_fixtures.py          # regenerate backend/tests/fixtures/llm/<Agent>/
cd backend && uv run python ../scripts/make_discovery_fixtures.py --check  # exit 1 if a fixture is missing, differs or is stale
```

Run it after you change an eval case, a sample document, an OpenAlex fixture, an InsightExtractor fixture, `backend/src/scout/discovery/domain/agent_io.py`, or any code that builds an agent's input. Each run writes `<hash>.input.json` next to the answer, so you can review what the agent saw.

The fixtures are keyed by the date, because the query years depend on it. They were recorded with the clock at 2026-10-21. After a change of year, the same inputs give different hashes, `--check` reports them, and the fake LLM falls back to `default.json`.

Field topics, keywords and the novelty bands are in `config/fields.yaml`. A domain expert reviews them in M7 (BACKLOG F-22).

## Evaluate the pipeline

```bash
make eval
```

`make eval` runs `eval/run_eval.py`. It runs every case in `eval/cases/` through the pipeline in memory, with no network. It writes `eval/reports/<timestamp>.json` and prints a Markdown table with these columns: shown count, verified-evidence rate, Critic rejection rate, `already_done_rate`, `duplicate_of_previous_rate`, theme recall, cost per run and wall time.

The command exits with status 1 when a case shows fewer than 3 or more than 10 opportunities, or when the verified-evidence rate is below 0.6. CI runs this check. The live backends (`--live`) need network and are not run in CI.

The cases are `eval/cases/foster.yaml` (three sample documents, one kept) and `eval/cases/moreira.yaml` (profile only). Each case lists the themes a domain reader expects. A theme is recalled when one shown opportunity contains all its keywords.

## Settings

All `RUN_*`, `ACCOUNT_DAILY_BUDGET_USD`, `FIELDS_CONFIG` and `ADMIN_CLAIMS_SHOW_VAULT_QUOTES` variables, with their defaults and meanings, are in [configuration](configuration.md#gap-analysis-runs). The same names, with safe defaults, are in `.env.example`.

## Tests

```bash
cd backend && uv run pytest tests/unit/discovery -q         # domain and application, fakes only; no database
make test-integration                                       # PostgreSQL: pipeline, resume after a kill, API
make eval                                                   # offline eval (CI threshold)
```

The unit tests cover the run state machine, the show rule, every check code, budgets and resume after a kill at each step. The integration tests `test_discovery_pipeline_pg.py`, `test_discovery_fixtures.py` and `test_discovery_api.py` run the pipeline on PostgreSQL, the eval cases with the fixtures, and the API with owner isolation and the admin role.

## Status

The backend for runs, opportunities, evidence and the admin claims view is in place (M3). The opportunity screens and the run progress view are built in M4.
