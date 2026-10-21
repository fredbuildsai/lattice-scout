# profile — bounded context

**Purpose:** the researcher profile built from OpenAlex: finding the user's author records, merging split records into one profile, importing their works and computing topics from the works the user keeps (ARCHITECTURE §3.2). Design: `docs/design/M1-identity-profile-claim.md` §3 to §5.

**Status:** M1 implemented (candidate search, claim, import job, works toggle). Methods from the vault (`researcher_method`) are empty until M5. Admin resolution of `contested` profiles arrives in M7. Matching does not yet skip `contested` profiles (M5).

**Layout** (same for every context):

| Folder | Holds |
|---|---|
| `domain/` | `names.py`, `grouping.py`, `researcher.py`, `work.py`, `topics.py`. Pure Python and Pydantic; no OpenAlex types. |
| `application/` | `ProfileService` (search, claim, read, toggle), `ImportProfile` (job handler), `mapping.py` (literature records to domain snapshots). |
| `ports/` | `ResearcherRepo`, `ClaimedWorkRepo`, `ProfileUnitOfWork`. |
| `adapters/` | `sql.py`: `SqlResearcherRepo`, `SqlWorkRepo`, `SqlProfileUnitOfWork`. |

The import rules are enforced by import-linter (`make contracts`). The domain cannot import `scout.ports.literature`; the application layer maps `AuthorRecord` and `WorkRecord` to domain types.

## Domain model

| Type | Kind | Meaning |
|---|---|---|
| `Researcher` | aggregate root, one per account | `display_name`, `orcid`, `institution`, `country`, `verified_level`, `import_status`, `import_generation`, `openalex_ids`, `topics`. |
| `ResearcherOpenAlexId` | entity of `Researcher` | One claimed OpenAlex author record: `author_id`, `merged` (False only for the primary), `basis`, `works_count`, `imported_generation`. |
| `ClaimedWork` | aggregate (own table) | One work attributed to the researcher: `openalex_work_id`, `title`, `year`, `cited_by`, `type`, `topics`, `source_author_ids`, `included`, `included_by_user`, `uncertain_reason`. |
| `ResearcherTopic` | value object | `topic_id`, `name`, `subfield`, `weight` in [0, 1] (largest = 1). |
| `AuthorSnapshot`, `GroupedRecord`, `CandidateGroup` | value objects | Search-side view of author records and candidate profiles (grouping). |
| `ImportStatus` | enum | `queued`, `importing`, `done`, `failed`. |
| `VerifiedLevel` | enum | `self_claimed` (default), `orcid_matched`, `contested`. |
| `RecordBasis` | enum | `primary`, `orcid`, `institution_name`, `manual`. |
| `UncertainReason` | enum | `non_article_type`, `name_mismatch`. |

### Grouping rules (`domain/grouping.py`, `group_candidates`)

Pure and deterministic: the result does not depend on the order of the input.

1. Records with the same non-empty ORCID form one group. The primary is the record with the most works, then the one with an institution, then the lowest id.
2. A record with no ORCID that shares an institution with a group and has a compatible name (`names_compatible`) is a `possible` record of the largest such group. It is never merged automatically. Weak links never chain.
3. Every other record is a group of its own.
4. Groups are ordered: an affiliation hint match first, then by works count (descending), then by primary id.

`names_compatible` (`domain/names.py`) ignores case, accents, order, punctuation and digits. Both names need at least two tokens. Each token of the shorter name must match a distinct token of the longer one, either exactly or as an initial. At least one full token must match exactly. "Foster E. Johan" is compatible with "Earl Johan Foster". A single word never is.

### Verification rules (`Researcher.recompute_verification`)

- `contested` if another account has claimed one of the same OpenAlex ids. Both profiles become `contested` internally; the API shows the level without the conflict, never `contested`, and never reveals the other account (DECISIONS 36).
- Otherwise `orcid_matched` if the primary record has an ORCID and every merged record was matched by that ORCID (basis `primary` or `orcid`).
- Otherwise `self_claimed`.

### Works rules (`domain/work.py`, `initial_inclusion`)

- A work starts ticked only if its type is `article` or `review` and no name at the claimed positions conflicts with the claimed records. Otherwise it starts unticked with `uncertain_reason`: `non_article_type` or `name_mismatch`.
- An absent author name (a truncated author list in OpenAlex) never counts against a work.
- Once the user ticks or unticks a work (`included_by_user = true`), re-imports update its metadata but never its `included` flag.

### Topics (`domain/topics.py`, `compute_topics`)

Topic scores are summed over **included** works only (a zero or missing score counts as 1.0). The sums are divided by the largest sum, so the strongest topic has weight 1. At most 25 topics are kept, sorted by weight and then id.

## Use cases

`ProfileService` (`application/service.py`), with `ProfileConfig` limits: 10 groups, 3 enriched groups, 10 claimed ids, 500 works per id, 25 topics.

| Use case | What it does |
|---|---|
| `find_candidates(name, affiliation)` | Searches OpenAlex by name, fetches the records with the ORCIDs of the first 3 hits, groups them and returns up to 10 groups. Uses at most 7 OpenAlex calls and no database. It reveals nothing about other users. |
| `claim(account_id, author_ids, primary_author_id, session_id)` | Validates 1 to 10 ids (`A` followed by digits, URL prefixes stripped; the primary must be in the list). Each id is checked against OpenAlex before any transaction opens (unknown id: 422 `unknown_author`). In one transaction it creates or re-claims the researcher, recomputes verification for every affected researcher, writes audit rows, and commits. Then it enqueues `profile.import`. |
| `get_profile(account_id)` | The researcher and all claimed works, or `ProfileNotFound` (404). |
| `summary(account_id)` | The researcher or `None`. Used by `GET /me`. |
| `toggle_work(account_id, work_id, included)` | Ticks or unticks one work and recomputes topics. Another user's work id gives `NotFound` (404), never 403. |

**Claim is idempotent.** The same ids and primary, with status `queued`, `importing` or `done`, change nothing and return the same `import_generation`. A queued claim is re-enqueued. A `failed` claim or a different set of ids bumps the generation, which starts a new import. An enqueue failure after the commit leaves the researcher `queued`; posting the same claim again re-enqueues it.

## Import job (`application/import_profile.py`, `ImportProfile`)

Job kind `profile.import`, at-least-once, resumable. The payload carries `researcher_id` and `generation`. Registered in `worker/loop.py` (`build_handlers`).

1. Load the researcher. If it is missing, its generation differs, or it is already `done`, the job is a no-op.
2. Set `importing`. For each claimed id that has not yet been imported in this generation, fetch its works (up to 500, outside any transaction), upsert them and mark that id as imported. Each id is one checkpoint, so a crash resumes without duplicates.
3. Delete source ids no longer claimed (and works left without any source), compute topics, copy institution and country from the primary record, and set `done`.

An error re-raises for the worker's back-off. On the last attempt (5 by default) the job first sets `failed` with `import_failed`. A claim record that OpenAlex no longer returns is skipped and logged.

## Ports

| Port | File | Production adapter | Test double |
|---|---|---|---|
| `ResearcherRepo` (`get_by_account`, `get`, `find_by_author_ids`, `add`, `save`) | `ports/repos.py` | `SqlResearcherRepo` | `InMemoryResearcherRepo` (`tests/fakes/profile.py`) |
| `ClaimedWorkRepo` (`list_for`, `get`, `upsert_many`, `save`, `delete_unsourced`) | `ports/repos.py` | `SqlWorkRepo` | `InMemoryWorkRepo` (`tests/fakes/profile.py`) |
| `ProfileUnitOfWork` (`researchers`, `works`, `audit`) | `ports/repos.py` | `SqlProfileUnitOfWork` | `InMemoryProfileUoW` (`tests/fakes/profile.py`) |
| `LiteraturePort` (cross-cutting) | `scout/ports/literature.py` | `OpenAlexClient`, or `FixtureLiterature` offline | `FixtureLiterature` (no separate in-memory fake yet) |
| `JobQueuePort` (cross-cutting) | `scout/ports/jobs.py` | `PostgresJobQueue` | `InMemoryJobQueue` |
| `AuditLog` (cross-cutting) | `scout/ports/audit.py` | `SqlAuditLog` | `InMemoryAuditLog` |

## Adapters and persistence

Migration `20261020_0003_identity_profile` creates these tables. SQLAlchemy Core definitions are in `scout/adapters/persistence/models.py`:

| Table | Content |
|---|---|
| `researcher` | One row per account (`account_id` unique). Status, generation, verification, institution, country. |
| `researcher_openalex_id` | Claimed author records; unique on `(researcher_id, openalex_author_id)`. |
| `claimed_work` | Works; unique on `(researcher_id, openalex_work_id)`; `topics` as JSON, `source_author_ids` as an array. |
| `researcher_topic` | Topic weights of the profile. |
| `researcher_method` | Created empty; written from M5. |
| `openalex_cache` | Raw OpenAlex responses (7-day TTL), used by `PostgresOpenAlexCache`. |

Every repository query filters by `account_id` or `researcher_id`. Replacing an aggregate replaces its child rows.

## Events and audit

Domain event `ProfileClaimed` is collected with `pull_events()`; there is no bus. The service writes these audit rows in the same transaction:

| Action | When | `meta` |
|---|---|---|
| `profile.claimed` | A new or changed claim | `{author_ids, generation}` |
| `profile.claim_conflict` | A claim shares an author id with another researcher | `{author_ids, other_researcher_ids}` |

## Endpoints

All endpoints need `Authorization: Bearer <token>`. Errors use problem+json.

| Method and path | Request | Response | Errors |
|---|---|---|---|
| `GET /api/v1/profile/candidates` | query `name` (2 to 200 characters), `affiliation?` | `CandidatesOut`: `groups[]` with `group_id` (the primary id), `primary`, `records`, `possible`, `includes_records`, `works_count`, `cited_by_count`, `orcid`, `institutions`, `country`, `top_topics` | 401, 422 `validation_failed` |
| `POST /api/v1/profile/claim` | `ClaimIn`: `author_ids` (1 to 10), `primary_author_id?` | **202** `ProfileOut` (`import.status` is `queued`) | 401, 422 `validation_failed`, 422 `unknown_author` (`author_id` in the body), 422 `conflicting_orcids` |
| `GET /api/v1/profile` | none | `ProfileOut`: `researcher_id`, `display_name`, `orcid`, `institution`, `country`, `verified_level`, `import` (`status`, `generation`, `error_code`, `requested_at`, `finished_at`), `openalex_ids`, `topics`, `works_summary` (`total`, `included`, `uncertain`), `works` (newest first, at most 500 per record) | 401, 404 `profile_not_found` |
| `PATCH /api/v1/profile/works/{work_id}` | `WorkPatchIn`: `included` | `WorkOut` | 401, 404 `not_found` (also for another user's work), 422 |

`ProfileOut` is returned by the claim endpoint with an empty `works` list. Poll `GET /api/v1/profile` every 2 seconds while `import.status` is `queued` or `importing`.

A queue failure at claim time answers 503 (`upstream_unavailable`); posting the same claim again recovers because the claim is idempotent.

## Invariants

1. Each input id appears exactly once across `records` and `possible`. The records of a group share one ORCID, or the group is a singleton. A record with ORCID X is never grouped with ORCID Y.
2. A `possible` record has no ORCID, shares an institution with the group and has a compatible name. `works_count` is the sum over the merged records.
3. Grouping does not depend on input order. `names_compatible` is symmetric.
4. A claim with two different ORCIDs is refused. One researcher per account. An author id claimed by two accounts makes both `contested`, and no endpoint reveals the other account.
5. Importing twice gives the same rows. A stale generation is a no-op. User toggles survive re-imports. Excluded works never count towards topics. Topic weights lie in [0, 1] with a maximum of 1.
6. Researcher data is returned only to its owner. Another user's profile or work gives 404.

## How to test

```bash
cd backend
uv run pytest tests/unit/profile -q                 # domain (Hypothesis), claim, import, crash resume; fakes and FixtureLiterature
uv run pytest tests/contract/test_literature.py -q  # FixtureLiterature and OpenAlexClient against the same fixtures
make test-integration                               # SQL repos, acceptance flow over HTTP on PostgreSQL
```

The acceptance flow (`tests/integration/test_profile_api.py`) signs in with `POST /api/v1/dev/login`, searches "Johan Foster", claims `A5059588273` and `A5140167166`, runs the import with the real handlers, then checks `GET /profile` and `GET /me`. The recorded fixtures are the public OpenAlex records of Johan Foster (`tests/fixtures/openalex/`, see its README).

For Johan Foster's fixture the search returns one group: primary `A5059588273` (159 works), with `A5140167166` (same ORCID, 1 work) in `records`, and `A5138740368` and `A5158972402` (no ORCID, UBC) in `possible`, unticked. The same search also returns single-record groups, for example `A5003854624`.
