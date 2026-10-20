# Profile: claim, grouping and import

The profile context turns an OpenAlex author record into the researcher's profile. It finds the records that belong to the signed-in person, merges split records, imports their works and computes topics from the works the person keeps. Code and rules: [profile module](https://github.com/fredbuildsai/lattice-scout/blob/main/backend/src/scout/profile/README.md). Design: `docs/design/M1-identity-profile-claim.md`.

## Claim flow over the API

Run the API and the worker, and sign in (see [authentication](auth.md)):

```bash
make dev                                  # migrates, then API, worker and the frontend if installed
# or, in separate shells:
make api                                  # uvicorn on :8000
make worker                               # python -m scout.worker; the import runs here
```

The steps:

1. **Search.** `GET /api/v1/profile/candidates?name=Johan%20Foster&affiliation=...` returns candidate groups, best match first.
2. **Choose.** The group's `group_id` is the primary record id. Put the records to merge in `records`; `possible` records are unticked by default.
3. **Claim.** `POST /api/v1/profile/claim` with `{"author_ids": [...], "primary_author_id": "..."}` answers **202** with `import.status = "queued"`.
4. **Wait.** Poll `GET /api/v1/profile` every 2 seconds while `import.status` is `queued` or `importing`. It ends at `done` or `failed`.
5. **Review.** `works_summary` gives total, included and uncertain counts. `PATCH /api/v1/profile/works/{work_id}` with `{"included": false}` changes one work.

```bash
curl -s "localhost:8000/api/v1/profile/candidates?name=Johan%20Foster" -H "Authorization: Bearer $TOKEN"

curl -s -X POST localhost:8000/api/v1/profile/claim -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"author_ids":["A5059588273","A5140167166"]}'

curl -s localhost:8000/api/v1/profile -H "Authorization: Bearer $TOKEN"
```

Claim input: 1 to 10 ids. Each is `A` followed by digits; a full OpenAlex URL is accepted and shortened. The primary must be one of the ids. Each id is checked against OpenAlex before anything is saved.

| Error | Status | Cause |
|---|---|---|
| `validation_failed` | 422 | Bad id, more than 10 ids, or primary not in the list |
| `unknown_author` | 422 | An id does not exist in OpenAlex (the id is in the body as `author_id`) |
| `conflicting_orcids` | 422 | The records carry two different ORCIDs |
| `profile_not_found` | 404 | `GET /profile` before any claim |

## Grouping

`group_candidates` in `backend/src/scout/profile/domain/grouping.py` works on the search hits and on the records that share their ORCIDs:

- Records with the same ORCID form one group (`basis = orcid`). The primary is the record with the most works.
- A record without an ORCID is a `possible` record of the largest group it may belong to. It shares an institution with that group and has a compatible name (`basis = institution_name`). It is never merged automatically. A weak link never joins a second group.
- Everything else is a group of one.
- Name compatibility (`names.py`) ignores case, accents, word order and punctuation, and allows initials. "Foster E. Johan" and "Earl Johan Foster" match. A single word never does.

The search uses at most 7 OpenAlex calls. It does not read the database, so it reveals nothing about other users.

## Import job (`profile.import`)

The claim commits, then enqueues one job with `dedupe_key = "<researcher_id>:<generation>"` and `max_attempts = 5`. The worker runs `ImportProfile` (`backend/src/scout/profile/application/import_profile.py`):

- It fetches up to 500 works per claimed record, outside any transaction. Each record is a checkpoint, so a crash resumes without duplicate rows.
- A work starts ticked only if its type is `article` or `review` and its author names agree with the claimed records. Otherwise it starts unticked with `uncertain_reason` `non_article_type` or `name_mismatch`. A missing author name never counts against a work.
- Re-imports update the metadata. They never change a work the user has ticked or unticked.
- Topics come from the included works only. The strongest topic has weight 1.
- Institution and country are copied from the primary record.
- A job for an old generation does nothing. A new claim with a different set of ids creates a new generation.
- On the last attempt, a failure sets `import.status = "failed"` with `error_code = "import_failed"`. Claiming again starts a new import.

If the status stays `queued`, the worker is not running.

Claims are idempotent. Posting the same ids and primary again returns the same `generation` and enqueues nothing new, unless the previous import failed.

## Verification levels

`verified_level` is recomputed on every claim, for the claiming researcher and for every other researcher that shares an author id:

| Level | Meaning |
|---|---|
| `self_claimed` | Default. The user chose the records. |
| `orcid_matched` | The primary record has an ORCID and every merged record was matched by that ORCID. |
| `contested` | Another account has claimed one of the same OpenAlex records. |

**Contested.** When two accounts claim the same author id, both researchers become `contested`. The claiming account gets a `profile.claim_conflict` audit row that lists the other researcher ids. No endpoint tells either user who the other is. Nothing resolves a contested profile yet: admin resolution arrives in M7, and matching (M5) must skip contested profiles. The API returns the level, but the web app does not show it yet.

## Persistence

Migration `20261020_0003_identity_profile` creates `researcher`, `researcher_openalex_id`, `claimed_work`, `researcher_topic`, `researcher_method` (empty until M5) and `openalex_cache`. Repositories are in `backend/src/scout/profile/adapters/sql.py`; every query filters by `account_id` or `researcher_id`.

## Literature source

`LITERATURE_BACKEND=fixtures` (default) serves the recorded responses in `tests/fixtures/openalex/`. These are the public OpenAlex records of Johan Foster and Raphaell Moreira; they need no network. `LITERATURE_BACKEND=openalex` calls the live API through `OpenAlexClient`, with `OPENALEX_API_KEY` and `OPENALEX_MAILTO` optional. Responses are cached for 7 days in `openalex_cache`. The live path is marked `needs_network` and is not run in the offline environment.

## Tests

```bash
cd backend
uv run pytest tests/unit/profile -q                 # names, grouping (Hypothesis), researcher, works, topics, claim, import with crash injection
uv run pytest tests/contract/test_literature.py -q  # literature port on fixtures and on a mocked HTTP transport
make test-integration                               # SQL repositories and the acceptance flow over HTTP on PostgreSQL
```

The acceptance flow in `backend/tests/integration/test_profile_api.py` covers: login, search, claim, import with the real handlers, `GET /profile` (159 works, 149 included, 10 uncertain, `orcid_matched`, two OpenAlex ids), and `GET /me` showing the researcher.
