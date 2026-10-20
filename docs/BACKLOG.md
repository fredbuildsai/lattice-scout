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
- **Deferred: docling as an optional `TextExtractionPort` adapter (OCR for scanned PDFs, PDF table structure, layout-aware Markdown).** Tried on 2026-10-20 (design note M2 §14, DECISIONS 46). `uv pip install docling` from PyPI resolved 121 packages, took 643 s and produced a 5.9 GB venv: the Linux torch wheel brings CUDA (nvidia-cudnn 528 MB, cublas 404 MB, torch 529 MB, triton 237 MB, and others). The CPU wheel index `download.pytorch.org` is blocked by the VM proxy. It was not run or benchmarked (timebox). It still needs: (1) a CPU-only torch source (a mirror or a vendored wheelhouse); (2) the layout and TableFormer model weights from Hugging Face, pre-downloaded into an image (`artifacts_path`) because offline workers cannot fetch them; (3) a separate worker image or an optional extra, never in CI's default lock; (4) a speed and determinism benchmark on the 30-page sample and a check that `prov.page_no` gives page offsets. Revisit if the pilot shows scanned uploads (`rejected: no_text_layer`) or poor table extraction
- `markitdown` as a DOCX table-to-Markdown helper: not adopted in M2 (DECISIONS 46); reconsider if DOCX tables matter for insights
- Vault: `POST /documents/{id}/retry` for `failed` documents with `retention=keep`; switch a kept document to `discard` from PATCH (design M2 §6)

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
| F-11 | backend | Vault full: `UploadDocument` passes `vault_limit_reached` as the message of `Conflict`, so the problem `code` is `conflict` and the UI shows "The upload failed. Please try again." instead of "Your vault is full". Give it its own error class with `code = "vault_limit_reached"`, and make `test_vault_limit_reached` assert the code (it accepts the code or the detail today). Files: `backend/src/scout/vault/application/upload.py`, `backend/tests/unit/vault/test_upload_document.py`. | med | fixed (VaultLimitReached, code vault_limit_reached) |
| F-12 | backend | An empty file gives 413 `file_too_large` (the `0 < len(data)` check in `UploadDocument.upload`), so the UI says "This file is larger than 25 MB." Give empty files their own error (422 `file_rejected` or 400) and a matching message in `frontend/src/features/vault/status.ts`. | low | fixed (422 file_rejected, reason empty_file) |
| F-13 | frontend | Page wording in `frontend/src/features/vault/DocumentRow.tsx`: every reference prints "p. N", also for Word files without page breaks (`page_basis = none`, always page 1), where design M2 §13 asks for "section". The count reads "1 pages" (no plural helper). Datasets are returned by the API but not shown. | low | open |
| F-14 | product | The delete dialog (`DeleteDialog` in `DocumentRow.tsx`) says "Opportunities already found stay, but can no longer cite this document." Opportunities do not exist before M3 and M4, so the sentence cannot be checked yet. Confirm it when M3 lands. | low | open, check at M3 |
| F-15 | frontend | The zip-bomb message ("This file was rejected because it unpacks to an unsafe size.") is shown only for a code the extractor never sends. A refused DOCX gets 422 `file_rejected`, whose text says "empty, damaged or protected". Align the texts with what the API sends. `GET /documents` also hides `deleting` rows, so the "Removing" chip is never shown. | low | open |
| F-16 | frontend | W3 vault: build the design's table (type, extracted, status, added columns) with category tabs, and the other file types (XLSX, HEIC, voice, cloud drive). M2 ships stacked cards (DECISIONS 53). | med | open |
