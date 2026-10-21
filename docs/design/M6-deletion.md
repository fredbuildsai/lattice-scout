# M6 slice A: account deletion and vault deletion (design note)

Scope: plan M6 task 2 (backend part of W10), ARCHITECTURE §4.3, §7, §8, §10, §11.2-11.3. Decisions 87-94 in `docs/DECISIONS.md`.

## 0. Acceptance (plan M6)
* Account deletion leaves no rows or files for the user. A test scans all tables and the bucket to prove it.
* Derived from §10 and the M5 invariants: the counterpart learns nothing beyond "the proposal became expired" (decline secrecy, M5 I3). Vault deletion removes every document's bytes, chunks and insights. A deleted account can never be recreated by a stale token.

## 1. Inventory (every table, verified against migrations 0001-0010)
"Cascade" means an existing `ON DELETE CASCADE` chain from `account`. Nothing in this table needs a new FK.

| Table | Link to the user | Strategy |
|---|---|---|
| `account` | `id`, `email` | **Hard delete**, last step, in the finish transaction (§3 step 6) |
| `researcher`, `researcher_openalex_id`, `claimed_work`, `researcher_topic`, `researcher_method` | `account_id` → `researcher_id` | Cascade |
| `document`, `document_chunk`, `document_insights` | `owner_id` → `document_id` | Cascade. Bytes go through the storage purge (§2) |
| `analysis_run`, `run_document`, `cluster`, `corpus_work`, `novelty_probe`, `opportunity`, `claim`, `evidence` | `owner_id` → `run_id`/`opportunity_id`/`claim_id` | Cascade. Unverified claims go too: D7 says "never deleted", but that rule is about pruning. The account owning the claims overrides it (decision 88) |
| `feedback` | `author_id` | Cascade. **B's** ratings and comments on a mutual match with A stay. They are B's data, and `target_id` is the match id, not A's id |
| `match_profile`, `match_profile_topic`, `match_profile_method`, `match_method_proposal` | `account_id` | Cascade |
| `match_block` | `account_id` or `blocked_account_id` | Cascade, both directions. A's account id is never reused (Supabase issues a new `sub`) |
| `match_proposal`, mutual | `a_id`/`b_id` | **Explicit delete** before the account delete. `joint_idea` cascades through the composite FK. The reveal was already mutual, so secrecy no longer applies (M5 Q3) |
| `match_proposal`, not mutual | `a_id`/`b_id` | **Explicit detach** before the account delete (§1.1). The row survives for B with the deleted side set to NULL |
| `app_session` | `account_id` (no FK) | Explicit delete |
| `analytics_event` (partitioned) | `account_id`, `session_id` (no FK) | Explicit delete: `account_id = :id OR session_id = ANY(:sessions)`. This also catches linked pre-login events |
| `llm_call`, `external_call` (partitioned) | `account_id`, `session_id`, `run_id` (no FK) | **Anonymise**: set `account_id`, `session_id`, `run_id` and `trace_id` to NULL where any of them matches. Cost rows stay, so the global budget guard (task 3) and `v_llm_usage_daily` stay correct |
| `job` | `payload`, `dedupe_key`, `result`, `last_error` (text) | Explicit delete of every row whose text contains a purge id (§3 step 6), except `kind = 'account.delete'`. The own row's payload is rewritten to `{subject_hash, pass}` |
| `audit_log` | `actor_id`, `target`, `meta` | Explicit delete of every row that mentions a purge id, **whoever the actor was** (for example `profile.claim_conflict`, `match.mutual`). Then one row is inserted: `account.deleted`, `actor_id NULL`, `target NULL`, `meta {subject_hash, requested_at, counts}`. No email, no email domain |
| `openalex_cache` | none (key = sha256 of the request, public responses) | Keep (7-day TTL) |
| `alembic_version`, `analytics.*` (views only) | none | Nothing |
| `notification` (task 1, if it adds the table) | `account_id` | MUST be `REFERENCES account ON DELETE CASCADE`. The scan test (§4) forces this |

**Purge ids** (`ErasureSubject`): `account_id`, `researcher_id`, `document_ids`, `run_ids`, `opportunity_ids`, `session_ids` and `mutual_match_ids`. They are collected while the account row still exists.

### 1.1 Matching detach (decline secrecy)
New domain method `MatchProposal.detach(side: Side, now) -> None`:
* `mutual` raises `InvariantViolation("detach_mutual")`. Mutual proposals are deleted instead.
* A pending state calls `self._decline(side, now, False, CloseReason.ACCOUNT_DELETED)`. This is the same path as `close_for_consent`, so B gets "awaiting_you"/"waiting_on_them" and then `expired`.
* Then: `<side>_id = None`. Clear `<side>_decision` and `<side>_decided_at`. Clear `never_similar_by` if it equals `side`. `rationale = rationale.without(side)`: the deleted side's `Perspective` becomes empty (`you_bring=[]`, `they_bring=[]`, `why=""`), and every `source_refs` in the survivor's `they_bring` is cleared (those refs point to A's works). `score_basis` (vocabulary labels and numbers) and both public facets (region, career band) are kept. B's card shows them, and changing them would leak (decision 89).
* `a_id`/`b_id` become `AccountId | None` in the domain. mypy strict then finds every use. Trap: in `DecideOnMatch`, `never_similar` must **not** write a `match_block` row when the other id is None. `side_of`, notifier calls and candidate SQL never see None for a live pair.
* `CloseReason` gains `ACCOUNT_DELETED`. It is never sent to either side (M5 §6).
* `matching.nightly` gets a step (f): delete proposals where `(a_id IS NULL OR b_id IS NULL) AND expires_at < now - 30 days`. B can no longer see them by then (M5 list rule).
* `MatchErasure.forget(account_id, now) -> ForgetResult(detached: int, deleted: int)` lives in `matching/application/erasure.py`. One transaction: `MatchRepo.involving_for_update(account_id)`, delete the mutual ones, `detach` and `save` the rest. Idempotent: a second call finds nothing.

## 2. Storage purge
* `FileStoragePort` gains `list_prefix(prefix: str) -> list[str]`, implemented in the in-memory fake, `LocalFileStorage` (rglob) and `SupabaseStorage` (the existing list call, paginated by `offset` until a short page). `SupabaseStorage.delete_prefix` loops "list, then delete" until the listing is empty (today it stops after 1000 objects).
* The job calls `delete_prefix(f"vault/{account_id}")` and then `list_prefix(...)`, which must return `[]`. Otherwise it raises and retries. This also removes orphan objects (decision 44) and `deleting` documents. Keys never contain anything else about the user (decision 44).

## 3. Flow, job and endpoints
**`POST /me/delete`** with body `{confirm_email: str}` returns **202** `{status: "deleting"}`. This deviates from §8 `DELETE /me` and from the brief's `/account/delete`; see decision 87.
* The endpoint depends on `current_principal`, not `current_account`. It calls `IdentityService.request_deletion(sub, confirm_email)`.
* Unknown account, or already deleting: 202, and the job is enqueued again (dedupe).
* `confirm_email` must equal the account email after `strip().lower()`, otherwise 422 `confirmation_mismatch`. There is no grace period, no undo and no re-auth requirement (decision 90).
* One transaction: `get_for_update`, `Account.request_deletion(now) -> bool` (sets `deleted_at`, turns both consents off, records the event `AccountDeletionRequested`), `save`, audit `account.deletion_requested` (erased later). After commit, enqueue `JobSpec(kind="account.delete", payload={"subject_id", "pass": 1, "feature": "account.delete"}, dedupe_key=f"erase:{hash}:1", max_attempts=10)`.
* **The payload key is `subject_id`, never `account_id`.** `worker/loop._context_from` would otherwise attribute the job's storage and auth `external_call` rows to the deleted user.
* Effect at commit: every endpoint answers 403 `account_deleted` (`ensure_active`). `match_pool_v` and `match_reveal_v` exclude the account. The frontend signs out and shows "Your account is being deleted".
* Catch-up for a lost enqueue: a self-rescheduling `identity.sweep` (hourly, `slot:` dedupe, `ensure_scheduled` at worker start, like `vault.sweep`) re-enqueues pass 1 for accounts with `deleted_at < now - 15 min`.

**`DeleteAccountJob.handle(job_id, payload, *, last_attempt)`** (`identity/application/erasure.py`). Every step is idempotent, so a retry restarts from step 0:
0. No `subject_id` in the payload (already finished): return `{"skipped": "done"}`. Otherwise compute `h = subject_hash(key, id)` and `subject = SubjectInventory.collect(id)`. A missing account gives a subject with only the account id and its session ids.
1. `MatchErasure.forget(id, now)`. This MUST run before step 6, because the cascade would delete the proposals and B's card would vanish.
2. Storage purge (§2).
3. `AuthAdminPort.delete_user(id)`. A 404 counts as success. An error raises and retries with back-off. On the last attempt, log `identity.auth_delete_failed` (hash only) and raise. The account stays soft-deleted and the sweep retries.
4. `ObservabilityErasure.erase(subject) -> {events_deleted, sessions_deleted, calls_anonymised}` (§1).
5. Pass 1 only: enqueue pass 2 (`run_at = now + ERASURE_SECOND_PASS_MINUTES` (15), dedupe `erase:{h}:2`). This happens **before** step 6, so a crash cannot lose it. It removes analytics and usage buffers flushed late, uploads that were in flight, and runs still going.
6. `AccountFinalizer.finish(subject, own_job_id, entry) -> {jobs_deleted, audit_deleted}`. **One transaction:** delete jobs, delete audit rows, insert `account.deleted` if no row with this hash exists yet, `DELETE FROM account WHERE id` (cascade), and `UPDATE job SET payload = {subject_hash, pass}, last_error = NULL WHERE id = own_job_id`.
7. Return `{subject_hash, pass, counts...}` as the job result, with no ids. Pass 2 runs the same steps (the account delete and the auth delete are no-ops) and does not enqueue a pass 3.

**Stale tokens:** `ensure_account` with no account row checks `AccountRepo.deleted_at_for(subject_hash) -> datetime | None`. It reads the `account.deleted` audit row through the partial index `audit_log ((meta->>'subject_hash')) WHERE action = 'account.deleted'`. If `principal.issued_at` (new `AuthPrincipal.issued_at: AwareDatetime | None`, from `iat`; dev tokens add it) is None or `<= deleted_at`, it raises `AccountDeleted` (403). A fresh dev login (same uuid5 sub, new `iat`) may create a new account.

**`subject_hash`** (`identity/domain/erasure.py`, stdlib `hmac`): `hmac_sha256(AUDIT_HASH_KEY, str(account_id)).hexdigest()`. The new setting has a dev default, and `APP_ENV=prod` refuses that default.

**Ports** (`identity/ports/erasure.py`). Structural typing: bootstrap wires the matching implementation, so there is no new import-linter exception.
```python
class SubjectInventory(Protocol):     async def collect(self, account_id: UUID) -> ErasureSubject: ...
class MatchErasurePort(Protocol):     async def forget(self, account_id: UUID, now: datetime) -> ForgetResult: ...
class ObservabilityErasure(Protocol): async def erase(self, subject: ErasureSubject) -> dict[str, int]: ...
class AccountFinalizer(Protocol):     async def finish(self, subject: ErasureSubject, own_job_id: UUID, entry: AuditEntry) -> dict[str, int]: ...
class AuthAdminPort(Protocol):        async def delete_user(self, user_id: UUID) -> None: ...   # idempotent
```
* `ErasureSubject` (a frozen Pydantic model in `identity/domain/erasure.py`) has `account_id: UUID`, `subject_hash: str`, `researcher_id: UUID | None` and `frozenset[UUID]` fields: `document_ids`, `run_ids`, `opportunity_ids`, `session_ids`, `mutual_match_ids`. Method `needles() -> list[str]` returns the string forms.
* SQL adapters go in `adapters/persistence/erasure.py`: `SqlSubjectInventory`, `SqlObservabilityErasure`, `SqlAccountFinalizer`. Text matching uses `col::text LIKE ANY(:patterns)`.
* Auth adapters: `SupabaseAuthAdmin` (`DELETE {SUPABASE_URL}/auth/v1/admin/users/{id}`, service-role key, contract test `needs_supabase`) and `DevAuthAdmin` (no-op, used with `AUTH_MODE=dev`).
* Fakes go in `tests/fakes/identity.py` and `tests/fakes/erasure.py`: they record calls and support `fail_next`.

**Vault-only deletion: `POST /documents/delete-all`** with body `{confirm: "DELETE"}` returns 202 `{deleting: int}`. Another literal gives 422 `confirmation_mismatch`.
* `VaultService.delete_all(owner_id)`, one transaction: lock every owner document that is not `deleting`. For each one, `request_deletion`, `chunks.delete_all` and `insights.delete`. Write one audit row `vault.cleared {count, originals_present}`. Commit.
* After commit, enqueue one `vault.delete` per document (the existing job; `vault.sweep` catches lost ones). Then enqueue `matching.profile {account_id}` (dedupe `profile:{id}`), so the vault-sourced method rows drop. Single `DELETE /documents/{id}` gets the same enqueue.
* Idempotent: a second call returns `{deleting: 0}`. Opportunities that lose vault evidence disappear through the read-time show rule (decisions 69 and 73). Profile, runs and matching are untouched.

## 4. Scan test (`tests/integration/test_account_erasure_pg.py`)
**Seed.** `tests/integration/erasure_seed.py` writes SQL Core inserts through `models.py` and one `INSERT` per observability table.
* User A gets at least one row in **every** base table: 2 documents (one `keep`, one `discard`) with chunks and insights, a run with clusters, corpus works, probes, opportunities, claims (verified and unverified) and evidence (with `document_id`), feedback, a match profile with topics, methods and a method proposal, an `app_session` with events (one pre-login event with `account_id NULL`), `llm_call` and `external_call` rows, jobs (`vault.delete`, `matching.reveal`, `profile.import`), and audit rows.
* A also gets 3 storage objects: 2 documents and 1 orphan under `vault/{A}`.
* Counterpart B gets: a pending proposal with A (A accepted), a mutual proposal with ideas, B's star rating on it, a proposal declined by B with a `match_block`, and B's own document, run and storage object.
* Run the API `POST /me/delete`, then `run_once` until idle, then advance `FixedClock` 15 minutes and drain again (pass 2).

**Scan.**
* Needles: the purge ids except `mutual_match_ids`, plus A's email, display name and ORCID. Match ids are shared objects and B's analytics may hold them legitimately.
* Walk `information_schema.tables` (`table_type = 'BASE TABLE'`, every schema except `pg_catalog`, `information_schema` and `pg_toast`) and `information_schema.columns` (every type except `bytea`). For each table, run `SELECT count(*) FROM t WHERE c1::text ILIKE ANY(:p) OR ...` with patterns `%needle%`. Every count MUST be 0, **with no allow-list**: everything that remains is needle-free by construction.
* **Coverage guard:** before deletion, every base table except `{alembic_version, openalex_cache, joint_idea}` and partition children has at least one needle row. A new table without deletion handling therefore fails the test.
* Bucket: `storage.list_prefix("vault")` contains no key with A's id, and B's object exists.
* Explicit assertions:
  * the mutual proposal and its `joint_idea` rows are gone;
  * the pending proposal exists with `a_id IS NULL` (or `b_id`), `close_reason = 'account_deleted'` and an empty perspective;
  * B's `GET /matches` shows that card as `waiting_on_them` and, after `expires_at`, as `expired`;
  * exactly one `account.deleted` audit row;
  * `llm_call` sums are unchanged;
  * B's rows are untouched;
  * the stale token gets 403 `account_deleted`;
  * `FakeAuthAdmin.deleted == [A]`.
* FK guard (same file): every FK whose `confrelid` is `account`, `researcher`, `document` or `analysis_run` has `confdeltype IN ('c','n')`.

## 5. Other tests
* **Unit (fakes)**, `DeleteAccountJob`:
  * step order;
  * failure injected at each step (storage, auth, finalizer), then a retry: the end state is identical, `account.deleted` is written once and pass 2 is enqueued once;
  * the job result and the rewritten payload contain no id;
  * the job context has no `account_id`.
* **Unit (fakes)**, `request_deletion`: confirmation mismatch, idempotency, consents off, 403 after.
* **Unit (fakes)**, `delete_all`: statuses, one audit row, N `vault.delete` jobs plus `matching.profile`, the second call returns 0.
* **Hypothesis** (extend the M5 `RuleBasedStateMachine`): a rule `detach(side)`.
  * Differential: B's observation log under `S + detach(A)` equals the log under `S + close_for_consent(A)`.
  * After detach: the side id is None, the deleted side's perspective is empty, the survivor's `they_bring` refs are empty, and `decide` by the survivor never yields a block on None.
  * `detach` on `mutual` raises.
* **Integration**: `SupabaseStorage.list_prefix` pagination and `delete_prefix` looping, with a mocked transport. `SupabaseAuthAdmin` contract test is skipped without credentials.

## 6. Persistence: migration `20261024_0011_account_erasure.py` (revision 0011)
* `match_proposal`:
  * `ALTER a_id, b_id DROP NOT NULL`. Keep `ON DELETE CASCADE` as the safety net: if detach is skipped, deletion wins over secrecy.
  * Add `CHECK (num_nonnulls(a_id, b_id) >= 1 AND (a_id IS NOT NULL OR state IN ('declined','expired')) AND (b_id IS NOT NULL OR state IN ('declined','expired')))`.
  * Replace the `close_reason` CHECK with `IN ('declined','consent_withdrawn','account_deleted')`.
* `audit_log`: the partial expression index from §3.
* Downgrade: delete detached rows, restore NOT NULL and the CHECK, drop the index.

## 7. Files, in implementation order
1. `identity/domain/erasure.py` (`subject_hash`, `ErasureSubject`), plus `Account.request_deletion` and the event, plus unit tests.
2. `matching/domain/proposal.py` (`detach`, Optional ids, `rationale.without`, `CloseReason.ACCOUNT_DELETED`), plus the Hypothesis rules. The test-engineer writes these first.
3. Migration 0011, then `models.py` (nullable `a_id`, `b_id`).
4. `ports/storage.py` `list_prefix`, plus the fake, local and Supabase adapters (delete loop).
5. `identity/ports/erasure.py` and `AuthPrincipal.issued_at` (jwks and dev verifier).
6. Adapters: `adapters/persistence/erasure.py` and `identity/adapters/auth_admin.py`. Fakes: `tests/fakes/erasure.py`.
7. `matching/application/erasure.py` (`MatchErasure`), `MatchRepo.involving_for_update`/`delete`, the `never_similar` None guard, nightly step (f).
8. `identity/application/erasure.py` (`DeleteAccountJob`), `IdentityService.request_deletion`, the stale-token check in `ensure_account`, and `identity.sweep`.
9. `VaultService.delete_all` and the `matching.profile` enqueue on delete.
10. `api/identity.py` `POST /me/delete`, `api/vault.py` `POST /documents/delete-all`, worker handlers (`account.delete` passes `job.id`; `identity.sweep`), bootstrap, settings (`AUDIT_HASH_KEY`, `ERASURE_SECOND_PASS_MINUTES`), `.env.example`, `make gen-client`.
11. `erasure_seed.py` and the scan, coverage and FK tests. Then the README (identity, vault) and the privacy user notes.

## 8. Risks and traps
* **Cascade before detach** deletes B's pending card: deletion becomes observable. Step 1 runs before step 6, and an integration test asserts that the card survives.
* **The job's own row**: payload, dedupe key and context must never carry the raw id after the finish step. Use the `subject_id` key, hash-based dedupe keys, and the payload rewrite in the finish transaction.
* **Late writers** (buffered analytics, usage meter, in-flight uploads, a run still going): FKs reject late rows on tables that have them. Pass 2 sweeps the tables without FKs and the bucket. The discovery and vault handlers must treat a missing run or document as `{"skipped": "gone"}`; add a test if they do not.
* Logs (stdout) carry `subject_hash` only during erasure. External log retention is outside the database and is documented in the privacy page.
* Kept on purpose:
  * B's free-text feedback comment, which might name A;
  * the 300-character claim paraphrases of deleted documents, until the product decides (decision 69);
  * the region and career band facet on detached proposals, for up to 30 days after expiry.
