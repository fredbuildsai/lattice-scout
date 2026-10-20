# identity — bounded context

**Purpose:** the account behind every signed-in user: the mapping from the verified token to an account row, the two consent flags and the account lifecycle (ARCHITECTURE §3.2, §4.3). Design: `docs/design/M1-identity-profile-claim.md` §2.

**Status:** M1 implemented (token verification, `GET /me`, `PATCH /me/consent`). The `deleted_at` field and its `account_deleted` check exist, but no use case sets it yet. Admin views (`role = admin`) arrive in M7.

**Layout** (same for every context):

| Folder | Holds |
|---|---|
| `domain/` | `Account` aggregate, events, errors. Imports nothing from the project. |
| `application/` | `IdentityService`: `ensure_account`, `get_account`, `update_consent`. |
| `ports/` | `AccountRepo`, `IdentityUnitOfWork` (`repos.py`); `AuthTokenVerifier`, `AuthPrincipal`, `InvalidToken` (`auth.py`). |
| `adapters/` | `sql.py` (SQL repo and unit of work), `jwks.py` (Supabase JWT verifier), `dev_auth.py` (dev key pair, issuer, verifier). |

The import rules are enforced by import-linter (`make contracts`).

## Domain model

`Account` (`domain/account.py`, aggregate root, one row per account):

| Field | Meaning |
|---|---|
| `id: AccountId` | The token's `sub`. Supabase user id, or in dev mode a deterministic UUID derived from the e-mail. |
| `email` | Lower-cased e-mail, synced from the token on every change. |
| `consent_matching` | Agrees to be matched with other researchers. Default `False`. |
| `consent_vault_signals` | Allows vault-derived topic signals in matching. Default `False`. Requires `consent_matching`. |
| `launch_field` | Set from `LAUNCH_FIELD` when the account is created. |
| `role` | `user` (default) or `admin`. |
| `created_at`, `updated_at`, `deleted_at` | Timestamps. `deleted_at` is set by no use case yet. |

Behaviour on the aggregate:

- `Account.create(...)`: both consents off; records `AccountCreated`.
- `set_consent(matching, vault_signals, now)`: turning matching off also turns vault signals off. Asking for vault signals while matching is off raises `ConsentRequiresMatching`. One `ConsentChanged` event per flag that really changed. A patch that changes nothing leaves `updated_at` untouched.
- `sync_email(email, now)`: adopts a changed e-mail from the token.
- `ensure_active()`: raises `AccountDeleted` for deleted accounts.

## Use cases

`IdentityService` (`application/service.py`), built with a unit-of-work factory, a clock, an id generator and the launch field.

| Use case | What it does |
|---|---|
| `ensure_account(sub, email)` | Returns the account for `sub`, creating it on the first call. A missing e-mail raises `EmailRequired`. If the insert loses a race with another request, the account is re-read and no second audit row is written. A changed e-mail is synced. Writes happen only on first call or e-mail change. |
| `get_account(account_id)` | Returns the account or `NotFound`. |
| `update_consent(account_id, matching, vault_signals)` | Applies a partial consent patch. An empty patch raises `ValidationFailed`. One audit row per flag that changed. |

`ensure_account` runs on every authenticated M1 request (through `current_account` in `api/deps.py`), so it costs one primary-key lookup per request.

## Ports

| Port | File | Production adapter | Test double |
|---|---|---|---|
| `AuthTokenVerifier` (`verify(bearer) -> AuthPrincipal`) | `ports/auth.py` | `SupabaseJwksVerifier` (AUTH_MODE=supabase) or `DevVerifier` (AUTH_MODE=dev) | none as a class; tests sign tokens with generated keys (`tests/fakes/jwt.py`) |
| `AccountRepo` (`get`, `add_if_absent`, `save`) | `ports/repos.py` | `SqlAccountRepo` | `InMemoryAccountRepo` (`tests/fakes/identity.py`) |
| `IdentityUnitOfWork` (`accounts`, `audit`) | `ports/repos.py` | `SqlIdentityUnitOfWork` | `InMemoryIdentityUoW` (`tests/fakes/identity.py`) |
| `AuditLog` (cross-cutting, `scout/ports/audit.py`) | `scout/ports/audit.py` | `SqlAuditLog` | `InMemoryAuditLog` (`tests/fakes/audit.py`) |

`add_if_absent` is `INSERT ... ON CONFLICT (id) DO NOTHING`. An e-mail already used by another id raises `EmailInUse`.

Wiring: `bootstrap._select_auth` picks the verifier from `AUTH_MODE`. The dev issuer exists only in `AUTH_MODE=dev`.

## Adapters

- `adapters/sql.py`: `SqlAccountRepo` and `SqlIdentityUnitOfWork` on the `account` table (`scout/adapters/persistence/models.py`).
- `adapters/jwks.py`: `SupabaseJwksVerifier` (signature, `iss`, `exp`, `role = authenticated`, `sub` must be a UUID) and `HttpJwksFetcher`. Allowed algorithms are ES256, RS256 and EdDSA only. Keys are cached for at most 10 minutes and refetched once for an unknown `kid`. The audience is not checked.
- `adapters/dev_auth.py`: `DevKeyPair` (ES256 key pair in `DEV_KEYS_DIR`), `DevTokenIssuer` (one-hour tokens), `DevVerifier`. Dev mode is refused when `APP_ENV=prod`. Details: [dev/auth.md](../../../../docs-site/pages/dev/auth.md).

## Events and audit

Events are collected with `pull_events()` after a change. There is no event bus. The application service writes the audit rows in the same transaction as the change:

| Event | Audit action | Audit `meta` |
|---|---|---|
| `identity.account_created` | `account.created` | `{email_domain}` (the domain only, not the address) |
| `identity.consent_changed` | `consent.changed` | `{consent, from, to}` |

## Endpoints

All endpoints need `Authorization: Bearer <token>`. Errors use RFC 9457 problem+json.

| Method and path | Body | Response | Errors |
|---|---|---|---|
| `GET /api/v1/me` | none | `MeOut`: `account_id`, `email`, `role`, `launch_field`, `consent_matching`, `consent_vault_signals`, `created_at`, `researcher` (summary or `null`) | 401 `invalid_token`, 401 `email_required`, 403 `account_deleted` |
| `PATCH /api/v1/me/consent` | `ConsentIn` (`consent_matching?`, `consent_vault_signals?`; unknown fields rejected) | `MeOut` | 401, 403 `account_deleted`, 422 `validation_failed`, 422 `consent_requires_matching` |
| `POST /api/v1/dev/login` (AUTH_MODE=dev only) | `{email}` | `access_token`, `token_type`, `expires_in` | 422 for an invalid e-mail |

## Invariants

1. After any sequence of changes, `consent_vault_signals` implies `consent_matching`.
2. One account row per `sub`. Concurrent first calls create one row and one `account.created` audit row.
3. Each real consent change produces one event and one audit row. A no-op patch writes nothing.
4. The e-mail is stored lower-case.
5. Account data is read only for the verified `sub` of the token.

## How to test

```bash
cd backend
uv run pytest tests/unit/identity tests/unit/api/test_auth_endpoints.py -q   # domain, service, tokens (fakes only)
make contracts                                                               # import-linter layering
make test-integration                                                        # SQL repos, race-safe ensure_account, /me and consent over HTTP
```

`tests/unit/identity/test_account_and_service.py` covers the aggregate and the service against the in-memory fakes. `tests/unit/identity/test_dev_auth.py` and `test_jwks_verifier.py` cover the two verifiers with generated keys. `tests/integration/test_profile_api.py` covers consent rules and the race-safe first call on PostgreSQL. Supabase itself is not reached from the VM. The live check is not yet run (see `docs/BLOCKERS.md`, B2).
