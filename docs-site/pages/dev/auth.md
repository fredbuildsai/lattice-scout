# Authentication

Every `/api/v1` endpoint needs a bearer token, except `POST /api/v1/events` (analytics) and `POST /api/v1/dev/login` (dev mode only). The API verifies the token, then creates the account on the first call (see [identity](https://github.com/fredbuildsai/lattice-scout/blob/main/backend/src/scout/identity/README.md)).

Two modes exist. `AUTH_MODE` (backend) and `VITE_AUTH_MODE` (frontend) must match.

| | `AUTH_MODE=dev` (default) | `AUTH_MODE=supabase` |
|---|---|---|
| Who signs the token | The API, with a local ES256 key pair | Supabase Auth |
| How a user signs in | `POST /api/v1/dev/login` with any e-mail, no password | Google, or an e-mail sign-in link |
| Token check | Signature against the local public key | Signature against the Supabase JWKS |
| Route `POST /api/v1/dev/login` | Mounted | Not mounted |
| Allowed when `APP_ENV=prod` | **No.** Startup fails. | Yes |
| Needs | nothing (keys are created on first start) | `SUPABASE_URL` (see below) |

## Dev mode

### Keys

On first start the API creates an ES256 (P-256) key pair in `DEV_KEYS_DIR` (default `var/dev-keys`):

- `private.pem` (PKCS8, file mode 0600) signs the tokens.
- `public.jwk` is the public key set, written for inspection. The verifier uses the key pair in memory; the JWKS is not served over HTTP.

`var/` is git-ignored. A relative `DEV_KEYS_DIR` is resolved from the current directory if it exists there, otherwise from the repository root. When the API runs from `backend/` (as `make api` does), the keys end up in `<repo>/var/dev-keys`. `scripts/dev_keys.py` uses that same location by default.

Deleting the directory creates a new key pair. Tokens signed with the old key then fail verification.

### Tokens

| Claim | Value |
|---|---|
| `sub` | UUID derived from the e-mail (the same e-mail always gives the same account) |
| `email` | the lower-cased e-mail |
| `role` | `authenticated` |
| `aud` | `authenticated` |
| `iss` | `DEV_ISSUER` (default `http://localhost:8000/dev-auth`; a label only, nothing is served there) |
| `exp` | one hour after issue |

### Run it

```bash
make dev                      # or: make api  (API on http://localhost:8000)

TOKEN=$(curl -s -X POST localhost:8000/api/v1/dev/login \
  -H 'Content-Type: application/json' -d '{"email":"you@example.edu"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
curl -s localhost:8000/api/v1/me -H "Authorization: Bearer $TOKEN"
```

The login response has `access_token`, `token_type` (`bearer`) and `expires_in` (3600). An invalid e-mail gives 422.

The frontend needs no extra setting: `VITE_AUTH_MODE` defaults to `dev`. Its sign-in form calls the same route and keeps the session in `localStorage`.

**Security.** Dev login checks nothing about the e-mail. Anyone who can reach the API can sign in as any address. Use it only on a developer machine, in tests and in e2e runs. The settings check refuses `AUTH_MODE=dev` with `APP_ENV=prod`.

## Supabase mode

### Settings

```bash
# backend/.env or the repository .env
AUTH_MODE=supabase
APP_ENV=dev                                     # or prod, with real backends
SUPABASE_URL=https://YOUR-PROJECT.supabase.co
SUPABASE_JWKS_URL=                              # optional; defaults to <SUPABASE_URL>/auth/v1/.well-known/jwks.json
```

`SUPABASE_URL` is required. The expected issuer (`<SUPABASE_URL>/auth/v1`) is derived from it. Setting only `SUPABASE_JWKS_URL` is rejected at startup with a clear settings error.

Frontend (`frontend/.env`, read by Vite; the repository-root `.env` is not read by Vite):

```bash
VITE_AUTH_MODE=supabase
VITE_SUPABASE_URL=https://YOUR-PROJECT.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=...               # the publishable key; it is public, not a secret
```

The frontend refuses to start the Supabase client without both `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`.

### What the API checks

`SupabaseJwksVerifier` (`identity/adapters/jwks.py`):

1. The header `alg` is one of `ES256`, `RS256` or `EdDSA`. HS256 and other algorithms are refused, so a shared secret cannot forge tokens. The header must carry a `kid`.
2. The key with that `kid` comes from the JWKS. Keys are cached for at most 10 minutes. An unknown `kid` triggers one refetch, to handle key rotation.
3. The signature is valid, `iss` equals `<SUPABASE_URL>/auth/v1`, and `exp`, `sub` and `iss` are present.
4. `exp` is in the future by the API clock.
5. `role` is `authenticated`, and `sub` is a UUID.

The audience (`aud`) is not checked. The principal's `app_metadata.role` is read but not used for authorisation yet.

Any failure gives 401 `invalid_token` (problem+json).

### Status

Supabase is not reachable from the development VM. The verifier is tested offline with generated keys (`backend/tests/unit/identity/test_jwks_verifier.py`). A check against a live project is pending (see `docs/BLOCKERS.md`, B2).

## Tests

```bash
cd backend
uv run pytest tests/unit/identity tests/unit/api/test_auth_endpoints.py -q
```

`test_dev_auth.py` covers key persistence, the token round trip, the 0600 file mode, the one-hour lifetime and tokens from another key pair. `test_jwks_verifier.py` covers the rejection cases (HS256 and `alg: none`, wrong issuer, expired token, wrong or missing role, non-UUID `sub`, unknown `kid`, JWKS fetch failure) and the 10-minute cache.
