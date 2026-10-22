# Account and vault deletion

How Scout erases a researcher. Design: `docs/design/M6-deletion.md`, decisions 87-94.

## Flow

1. `POST /api/v1/me/delete` with `{"confirm_email": "..."}` checks the typed e-mail, sets `deleted_at`, turns both consents off and queues `account.delete`. Every later request of that account gets 403 `account_deleted`.
2. The job `account.delete` (handler `DeleteAccountJob`) runs these idempotent steps. A retry starts again from the top:
    1. `MatchErasure.forget`: mutual proposals are deleted, the others are *detached* (the deleted side becomes `NULL`, its text and source references are cleared). This must run before the account row goes, because the foreign key would otherwise delete the counterpart's card and reveal the deletion.
    2. Storage purge of `vault/{account_id}`. The job fails if the bucket still lists objects.
    3. Removal of the user from the sign-in provider (`SupabaseAuthAdmin`; a no-op with `AUTH_MODE=dev`).
    4. Analytics rows and sessions are deleted. `llm_call` and `external_call` lose their user, session, run and trace columns (cost totals stay).
    5. Pass 1 queues pass 2 (`ERASURE_SECOND_PASS_MINUTES`, default 15) for late writers.
    6. One transaction deletes jobs and audit rows that mention the user, writes the tombstone and deletes the account (cascade).
3. `identity.sweep` runs hourly and re-queues deletions whose job was lost.

The job payload uses the key `subject_id`, never `account_id`, so the worker does not tag its own log rows with the deleted user. After the last step the payload holds only `{subject_hash, pass}`.

## What remains

- One audit row `account.deleted` with an HMAC-SHA256 of the account id (key `AUDIT_HASH_KEY`), the counts, and no e-mail.
- Cost rows without a user.
- The counterpart's own data, including their free-text feedback on a mutual match.
- Detached proposals for up to 30 days after they expire.
- Logs outside the database are not touched.

A token issued before the deletion can never recreate the account (403). A new sign-in creates a new, empty account.

## Adding a table

Give any new table that holds user data a foreign key to `account` (or `researcher`, `document`, `analysis_run`) with `ON DELETE CASCADE`, or handle it in `adapters/persistence/erasure.py`. The test `test_every_base_table_holds_a_needle_before_deletion` fails until `tests/integration/erasure_seed.py` writes a row for it, and `test_account_deletion_scan` then proves that the row is gone.

## Vault only

`POST /api/v1/documents/delete-all` with `{"confirm": "DELETE"}` marks every document for deletion (text, chunks and insights go at once, files through `vault.delete`) and queues `matching.profile`, so method signals taken from the vault drop out of the profile.
