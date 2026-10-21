# Private vault: upload, ingestion and retention

The vault holds a researcher's own documents (grants, proposals, unpublished results, lab notes) as PDF or DOCX. The API stores the file, a worker reads it, and the insights are kept only when they can be checked against the text. Code and rules: [vault module](https://github.com/fredbuildsai/lattice-scout/blob/main/backend/src/scout/vault/README.md). Design: `docs/design/M2-vault-ingestion.md`. Decisions: `docs/DECISIONS.md` 41 to 52, and ADR-009 for retention.

## Architecture

The context follows the hexagonal layout used by every context (see [developer guide](index.md)):

| Layer | Vault code | Notes |
|---|---|---|
| Domain | `backend/src/scout/vault/domain/` | `Document` state machine, chunking, verification, retention rules, upload checks. Stdlib and Pydantic only. |
| Ports | `backend/src/scout/vault/ports/` | `TextExtractionPort`, `InsightExtractorPort`, repositories, `VaultUnitOfWork`. Shared ports: `scout/ports/storage.py` (`FileStoragePort`) and `scout/ports/llm.py`. |
| Application | `backend/src/scout/vault/application/` | `UploadDocument`, `IngestDocument`, `ExtractInsights`, `VaultService`, `DeleteDocumentJob`, `SweepVault`. |
| Adapters | `backend/src/scout/vault/adapters/` | `PdfDocxExtractor` and its child process, `AgentsInsightExtractor`, `SqlVaultUnitOfWork`. Storage: `backend/src/scout/adapters/storage/` (`local.py`, `supabase.py`). Agents: `backend/src/scout/adapters/llm/factory.py`. |
| HTTP | `backend/src/scout/api/vault.py`, `api/schemas/vault.py` | Five endpoints under `/api/v1/documents`, all behind `current_account`. |

`bootstrap.py` wires the adapters. The worker runs the three jobs: `vault.ingest`, `vault.delete` and `vault.sweep`.

## Upload to ready

1. **Upload.** `POST /api/v1/documents` checks the size (`0 < n <= VAULT_MAX_BYTES`) and the type by content (`%PDF-` in the first 1 KB, or a ZIP with `[Content_Types].xml` and `word/document.xml`). The declared type and the extension are ignored. DOCX archives also pass the zip limits (1000 entries, 200 MB uncompressed, compression ratio 100).
2. **Duplicates.** The sha256 is looked up among active documents of the same owner. A match returns that document with HTTP 200 and creates nothing.
3. **Store, then record.** The bytes go to `vault/{owner_id}/{document_id}` (never the filename), outside any transaction. Then the row and the `document.uploaded` audit row are written in one commit. If the commit fails, the object is deleted; if that also fails, `vault.orphan_object` is logged.
4. **Queue.** `vault.ingest` is enqueued with `dedupe_key = document id` and `max_attempts = 4`. If the enqueue fails, the request still returns 201 and the sweeper recovers the document.
5. **Read.** The job moves `uploaded` to `extracting` and runs the extractor in a child process. The chunks are stored, and the status becomes `extracted`.
6. **Analyse.** The job moves to `analysing`, calls the `InsightExtractor` agent with no transaction open, verifies the output, and stores it. The status becomes `ready`.
7. **Purge.** For `discard` documents, the same commit as `ready` replaces the chunk text with quotes and summaries. Then the original bytes are deleted and `document.original_discarded` is written.

The job looks at the current status and does the next step, so a re-run after a crash gives the same chunks and insights. A failed step on its last attempt marks the document `failed` with a code (see the table of codes below). `rejected` is used when the file can never be read, for example an encrypted file, and is not retried.

## Retention (D6, ADR-009)

`RETENTION_POLICY` sets the deployment default. The effective setting of each document is fixed at upload.

| `RETENTION_POLICY` | Per-document result | Form field `keep_original` |
|---|---|---|
| `keep` | Bytes and chunk text kept until `DELETE`. | Ignored. |
| `discard` | Bytes deleted right after the terminal status. Chunk text set to `NULL`; quotes and summaries kept. | Ignored. |
| `user_choice` (default) | As chosen by the user. | `true` keeps; absent or `false` discards. |

Changing the policy:

1. Set `RETENTION_POLICY=keep`, `discard` or `user_choice` in `.env` (or in the environment of the API process).
2. Restart the API. The value is read when the upload use case is built, so only uploads after the restart use it.
3. Existing documents keep their own setting. Changing a kept document to `discard` is not supported yet (BACKLOG).

The default `user_choice` is provisional. ADR-009 is still `proposed`, and the product owner confirms the default before the pilot.

What a discarded document keeps, per chunk, is decided by `retained_content` (`backend/src/scout/vault/domain/retention.py`): verified reviewer-objection quotes of at most 300 characters (with a `truncated` flag when cut), and a summary of the verified items that cite the chunk, at most 300 characters. Chunks nobody cites keep only their ids, offsets and page numbers. The stored insights record is bounded too: under `discard`, `bounded_for_discard` cuts every string in it (verified or not) to 300 characters in the same commit, and a quote longer than 300 characters is never verified (`quote_too_long`). Model output is also stripped of control characters and each string is capped at 600 characters before anything is stored.

## Text extraction: choice and alternatives

The default extractor is `PdfDocxExtractor` (`backend/src/scout/vault/adapters/extraction.py`).

| Format | Library | Why |
|---|---|---|
| PDF | **pypdf** (BSD-3) | Fast (about 130 to 140 ms for a 30-page proposal on the VM), pure Python, exact physical page numbers. |
| PDF fallback | **pdfplumber** (MIT) | Used only when pypdf raises or finds fewer than 200 characters. Slower (2.3 to 3.7 s for the same file). |
| DOCX | **python-docx** (MIT) | 26 to 49 ms, and page breaks give page numbers. |

Measurements are from the design note (`docs/design/M2-vault-ingestion.md` §14), taken on the VM with a 30-page PDF and its DOCX twin. All candidates were deterministic and offline, and none performed OCR.

Rejected and deferred:

- **markitdown: rejected.** It needs a 223 MB environment (onnxruntime, magika). It lost the page breaks in DOCX files and gives PDF pages only through `\f` separators. It is about 25 times slower than pypdf on PDF.
- **docling: deferred** (BACKLOG). It is the only option with OCR and table structure, but the PyPI install took 643 s and produced a 5.9 GB environment (CUDA torch). The CPU wheel index is blocked by the proxy, and the layout models must be downloaded from Hugging Face and baked into an image. It was not run.

`TextExtractionPort` does not depend on the library, so either can be added later as another adapter. Scanned PDFs are `rejected: no_text_layer` until OCR exists.

### Child process and limits

Parsing runs in a child process (`python -I -m scout.vault.adapters.extract_child`), so a hostile file cannot hang or exhaust the worker. The bytes go in on stdin and one JSON line comes back on stdout. Limits:

| Limit | Setting | Result when exceeded |
|---|---|---|
| Size of the upload | `VAULT_MAX_BYTES` (25 MiB) | HTTP 413 at upload |
| Pages | `VAULT_MAX_PAGES` (300) | `too_many_pages` |
| Total characters | `VAULT_MAX_TEXT_CHARS` (720 000, 12 slices) | `text_too_long` |
| Address space | `VAULT_EXTRACT_MEMORY_MB` (1024), `RLIMIT_AS` | `too_complex` (MemoryError in the child) |
| CPU time | `VAULT_EXTRACT_TIMEOUT_S` (60), `RLIMIT_CPU` | `extraction_timeout` |
| Wall-clock | `VAULT_EXTRACT_TIMEOUT_S + 5` s, then the parent kills the child | `extraction_timeout` |
| Under 200 characters | fixed | `no_text_layer` (PDF; DOCX only when it has no text at all) |

Encrypted PDFs are tried with the empty password; if that fails, `encrypted`. A child that exits non-zero or prints unreadable output gives `corrupt_file`. The child never writes document text to a log or to stderr (stderr is discarded).

## Agent factory and prompt versioning

`AgentFactory` (`backend/src/scout/adapters/llm/factory.py`) is the one way the application runs an agent:

- `spec(name, output_type)` loads `prompts/<name>.md` (front matter plus body) and fills `{{placeholder}}` values. Bootstrap passes `field_name` (from `LAUNCH_FIELD`) and `today`. An unknown placeholder raises `ValidationFailed` when `spec()` is called. Bootstrap calls it at start-up, so a broken prompt stops the process before it serves requests. Specs are cached per name.
- `run(spec, input, ctx)` runs inside the span `agent.run`. Its attributes are the agent name, prompt version, tier, model, route, attempts, tokens, `cost_usd` and `input_chars`. It logs `agent_done` with the same fields. Neither the input nor the output is put in spans, logs or exceptions raised by the factory. Database errors are described by class, SQLSTATE and constraint name only (`describe_exception`), the worker span does not record exceptions and sets a scrubbed status, and the API logs frames, not the exception message.
- Errors: output that does not match the schema becomes `LLMOutputInvalid("<name>: output invalid")`. Other infrastructure and provider failures become `LLMUnavailable("<name>: unavailable")`. Both are raised `from None`, because a chained `ValidationError` would carry the document text.
- The tier (`fast` or `reasoning`) comes from the front matter. The adapters map it to a model route. The factory never chooses a model.

**Prompt versioning.** `prompts/InsightExtractor.md` has a `version` and a `changelog` in its front matter (currently `1.1.0`). The stored value is `InsightExtractor@1.1.0`. It appears in `StoredInsights.prompt_version`, in `agent_done`, and in the traces. When the output shape changes:

1. Change the Pydantic model in `vault/domain/insights.py` and the JSON shape in the prompt in the same commit.
2. Bump `version` and add a `changelog` line (see `prompts/README.md`).
3. Re-check the fixtures (below). The fixture key is a hash of the request input, not of the prompt, so a prompt change alone does not change the key.

## Insight verification rules

The LLM returns items with chunk labels (`c0`, `c1`, ...). `verify` (`vault/domain/verification.py`) checks each one before it is stored:

- An item is **unverified** when it has no chunk reference, when any label is not a chunk of the document, or when any string in it contains an email address.
- A reviewer objection is also unverified when its normalised quote has fewer than 15 characters, or when the quote is not a substring of its cited chunks joined in reading order. A quote cited from non-adjacent chunks is matched against each run of consecutive chunks separately, so it cannot be stitched together. The match is verbatim after `norm` (NFKC, straight quotes, plain dashes, joined hyphenation, collapsed whitespace; case kept).
- A map-reduce run verifies each map partial, sends only the verified items to the reduce call, and then verifies the final output against all chunks.

Unverified items are stored in `document_insights.payload` with a `reason` and a `stage`. They are never returned by the API: `StoredInsights.visible()` is the only source for `GET /documents/{id}`. The stored counts (`verified_count`, `unverified_count`) are not exposed to users.

## Regenerating the sample documents and the LLM fixtures

The sample documents are fictional. Their Markdown sources are in `tests/fixtures/vault/*.md`, and `README.md` there describes them.

```bash
cd backend && uv run python ../scripts/make_sample_docs.py          # PDF and DOCX into tests/fixtures/vault/build/ (git-ignored)
cd backend && uv run python ../scripts/make_sample_docs.py --long   # also the 30-page proposal, sample-long-proposal-30p.pdf
cd backend && uv run python ../scripts/make_insight_fixtures.py --check   # exit 1 if a fixture is missing or differs
cd backend && uv run python ../scripts/make_insight_fixtures.py           # regenerate backend/tests/fixtures/llm/InsightExtractor/
```

`make_insight_fixtures.py` renders each sample in memory, runs the real extractor and chunker, captures the request that `AgentsInsightExtractor` sends, and writes one file per request. The file name is the hash of the request. The contents are hand-written, but every label and quote is located in the real chunk text, so the fixtures always pass `verify`. A library upgrade (pypdf, reportlab) can change the text and therefore the hash (DECISIONS 52). DOCX samples have no fixtures.

The frontend end-to-end test (`frontend/e2e/vault.spec.ts`) expects `tests/fixtures/vault/build/declined-proposal-lignin-foams.pdf`.

## Adding a storage backend

The port is `FileStoragePort` (`backend/src/scout/ports/storage.py`): `put`, `get`, `delete`, `delete_prefix` and `exists`. Keys match `^[a-z0-9][a-z0-9/_-]*$` (`validate_key`), and the vault never puts a filename in a key.

1. Write the adapter in `backend/src/scout/adapters/storage/<name>.py`. `get` raises `StorageObjectMissing` for a missing key. `delete` is idempotent. Validate every key with `validate_key`.
2. Add the name to the `storage_backend` `Literal` in `backend/src/scout/settings.py`, and validate its settings in the same file (see how `supabase` is checked).
3. Add a `case` to `_select_storage` in `backend/src/scout/bootstrap.py`. The `match` ends in `assert_never`, so mypy flags a missing case.
4. Add a parameter to `backend/tests/contract/test_file_storage.py`. Mark it `needs_supabase` if it needs live credentials. The contract covers round trip, overwrite, missing get, idempotent delete, prefix delete and key validation.
5. Document the variables in `.env.example` and [configuration](configuration.md).

`APP_ENV=prod` already refuses `STORAGE_BACKEND=local`.

## API reference

All endpoints need a bearer token. Errors are problem+json (`code` and `detail`). Use the OpenAPI document at `/docs` for schemas.

| Method and path | Request | Success | Errors |
|---|---|---|---|
| `POST /api/v1/documents` | multipart: `file` (required), `keep_original` (bool, default `false`), `kind` (optional) | 201 with `Location`, `DocumentOut`. 200 when the same bytes are already in the vault. | 413 `file_too_large`; 415 `unsupported_file_type`; 422 `file_rejected` (an empty file, or a DOCX that is a zip bomb or corrupt) or `validation_failed`; 409 `vault_limit_reached` with `limit`; 401 `unauthenticated` |
| `GET /api/v1/documents` | none | 200 `DocumentListOut {items, next_cursor: null}`. Newest first, at most 50. Excludes `deleting`. | 401 |
| `GET /api/v1/documents/{id}` | none | 200 `DocumentDetailOut`: `DocumentOut` plus `insights` (verified items only, with `refs` of `chunk_id`, `page_from`, `page_to`), or `null` | 404 `not_found` (also for another owner's document and for `deleting`); 401 |
| `PATCH /api/v1/documents/{id}` | JSON `{"kind": "<DocKind>"}` only | 200 `DocumentOut` | 404 `not_found`; 422 `validation_failed`; 401 |
| `DELETE /api/v1/documents/{id}` | none | 204. Chunks and insights are gone at commit; `vault.delete` removes the object. | 404 `not_found`; 401 |

Notes:

- `UploadGuard` (`backend/src/scout/api/upload_guard.py`) runs before FastAPI parses the multipart body: it answers 401 for a missing or invalid token and 413 above the limit plus 64 KB (from `Content-Length`, or by counting streamed bytes for chunked bodies), without reading the file.
- An **empty** file is refused with 422 `file_rejected` (reason `empty_file`). Type sniffing, the archive checks and the SHA-256 run in a worker thread; an archive that declares more than 1000 entries is refused before its directory is parsed.
- `vault_limit_reached` is meant to be the code of the 409. Today the code is `conflict` and the text `vault_limit_reached` is in `detail` (BACKLOG F-11). Clients should match on the `detail` until that is fixed.
- The count against the limit includes `failed` and `rejected` documents, until they are deleted.
- The frontend polls `GET /api/v1/documents` every 2 seconds while a document is `uploaded`, `extracting`, `extracted` or `analysing` (`frontend/src/features/vault/`).

## Document statuses

| Status | Meaning | Set by |
|---|---|---|
| `uploaded` | Stored, not yet read. | `POST` |
| `extracting` | Being read. | `vault.ingest` |
| `extracted` | Chunks stored, analysis not started. | `vault.ingest` |
| `analysing` | Insight extraction running. | `vault.ingest` |
| `ready` | Insights stored. Terminal. | `vault.ingest` |
| `failed` | Retries exhausted. `failure_code` says why. Terminal. | last attempt of `vault.ingest`, or the sweeper |
| `rejected` | The file can never be read. `failure_code` says why. Terminal. | `vault.ingest` |
| `deleting` | Tombstone. Not returned by any read. Removed when the object is gone. | `DELETE` |

`failure_code` values: `encrypted`, `no_text_layer`, `too_many_pages`, `text_too_long`, `corrupt_file`, `zip_bomb`, `extraction_timeout`, `too_complex` (rejected); `original_missing`, `ingest_stalled`, `extraction_failed`, `insights_unavailable`, `storage_unavailable` (failed). The frontend turns these into messages in `frontend/src/features/vault/status.ts`. `zip_bomb` is never produced by the extractor today: DOCX zip limits run at upload and return 422 `file_rejected`.

## Jobs

| Job | Behaviour (details in the module README) |
|---|---|
| `vault.ingest` | Extract, analyse, purge, depending on the status. Skipped when the document is missing or `deleting`. |
| `vault.delete` | Delete the object, then the row. A storage failure is retried and logged as `vault.storage_delete_failed`. |
| `vault.sweep` | Every 10 minutes, scheduled when the worker starts. Re-enqueues stale work, fails stalled work after 60 minutes, and reschedules itself first. |

Run the worker with `make worker`. Without a running worker, uploads stay `uploaded`. When the worker starts, it schedules the sweep, and the sweep re-enqueues uploads that are older than 10 minutes.

## Logs and audit

Logs and audit rows carry ids, codes and counts only. The canary test (`backend/tests/unit/vault/test_no_leak.py`) puts a marker string into a document and checks that it appears in no audit row, job, log, exception or trace; `backend/tests/integration/test_vault_privacy.py` repeats the check against PostgreSQL (database errors, NUL characters in model output) and covers delete-during-analysis and the upload guard. The full list of log names is in the module README.

## Tests

```bash
cd backend && uv run pytest tests/unit/vault tests/contract/test_file_storage.py -q   # unit and contract, no database
make test-integration                                                                  # PostgreSQL: repositories and the API acceptance flow
cd backend && uv run pytest tests/integration/test_vault_api.py -m integration -q     # only the vault API tests
```

The integration test `test_acceptance_30_page_proposal_discard_then_delete` uploads the 30-page proposal, runs the worker until it is `ready`, checks the reviewer quotes and page references, and checks that `DELETE` removes the rows and the object. The live LLM is never called in tests: the fake LLM reads the fixtures.
