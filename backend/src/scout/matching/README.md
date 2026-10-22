# matching: bounded context

**Purpose:** researcher matching profiles, complementarity proposals, double opt-in decisions, the reveal of contact
details and joint ideas, and the match-level ratings (ARCHITECTURE §3.2 and §6.3, §6.4).
**Key aggregate:** `MatchProposal`. **Design:** `docs/design/M5-matching.md`. **Decisions:** `docs/DECISIONS.md` 77 to 86.
**Status:** M5 backend. Notifications are a logging stub until M6. The screens are in `frontend/src/features/matches/`.

## Layout

| Folder | Holds |
|---|---|
| `domain/` | `MatchProposal` and its state machine, `reveal.py` (views and the reveal rule), `profile.py` and `scoring.py` (pure), `guards.py` (text guards), `agent_io.py` (agent I/O models), `enums.py`, `ids.py`, `regions.py`, `vocabulary.py`. |
| `application/` | `MatchingService` (reads), `DecideOnMatch`, `ComputeResearcherProfile`, `ComputeMatches`, `RevealMatch`, `MatchingNightly`, `MatchConfig`, and `sources.py` (the anti-corruption layer). |
| `ports/` | `MatchRepo`, `MatchProfileRepo`, `CandidateQuery`, `JointIdeaRepo`, `RevealRepo`, `BlockRepo`, `MatchUnitOfWork`, `ProfileSource`, `ConsentSource`, `VaultSignalSource`, `MatchAgentsPort`, `MatchNotifier`. |
| `adapters/` | `sql.py` (SQLAlchemy repositories and the candidate query), `agents.py` (`AgentsMatching` over `AgentFactory`), `vocabulary.py`, `notifier.py` (`LoggingMatchNotifier`). |

Import rules are enforced by import-linter (`make contracts`). `application/sources.py` is the only matching module that
imports `ProfileService`, `VaultService` and `IdentityService`. The composition root is `scout/bootstrap.py`.

## Domain model

- **`MatchProposal`** (aggregate). Ids are canonical: `a_id < b_id`. It holds the two decisions, the state, the
  expiry, the score and its basis, the stored rationale, and one **public facet** per side (`region`, `career_band`).
  The facets are copied at proposal time, so the anonymous card never joins identity tables.
- **Ids.** `match_id = uuid5(MATCHING_NS, "a:b:<ISO week>")`. `idea_id = uuid5(MATCHING_NS, "<match_id>:idea:<ord>")`. The same pair in the
  same ISO week always gets the same id.
- **Events.** `MatchProposed` (`matching.proposed`) and `MatchBecameMutual` (`matching.mutual`). There is **no decline
  event**, so nothing downstream can leak a decline.

## State machine

States: `proposed`, `a_accepted`, `b_accepted`, `mutual`, `declined`, `expired`. `mutual`, `declined` and `expired` are
terminal. "Own" is the viewer's side; "other" is the other side. Source: `MatchProposal.decide()`.

| State before | Viewer action | Result |
|---|---|---|
| any pending state, `now >= expires_at` | any | expire, then 409 `match_expired` |
| `proposed` | accept | own accept (`a_accepted` or `b_accepted`) |
| `proposed` | decline | `declined`, `declined_by` = viewer |
| `proposed` | withdraw | 409 `nothing_to_withdraw` |
| own accepted | accept | no-op |
| own accepted | decline | `declined` |
| own accepted | withdraw | back to `proposed` |
| other accepted | accept | `mutual`, `revealed_at` set, `MatchBecameMutual` |
| other accepted | decline | `declined` |
| other accepted | withdraw | 409 `nothing_to_withdraw` |
| `mutual` | accept | no-op |
| `mutual` | decline or withdraw | 409 `match_closed` |
| `declined` by viewer | decline | no-op |
| `declined` by viewer | accept or withdraw | 409 `match_closed` |
| `declined` by other | accept or decline | recorded silently, returns OK |
| `declined` by other, viewer already accepted | withdraw | own decision cleared, returns OK |
| `declined` by other, viewer has no decision | withdraw | 409 `nothing_to_withdraw` |
| `declined` by other, viewer already declined | accept or withdraw | 409 `match_closed` |
| `expired` | any | 409 `match_expired` |

`never_similar` is valid only with decline (otherwise 422 `never_similar_needs_decline`). It sets `never_similar_by`,
and the use case writes a `match_block` row in both directions. It also applies to a silently recorded decline.

**What each side is told.** `status_for(viewer, now)` is a pure function. It never reads the other side's decision,
except through `mutual`:

| Condition (checked in this order) | Status shown |
|---|---|
| state `mutual` | `mutual` |
| declined by viewer, or viewer declined | `declined_by_you` |
| state `expired`, or `now >= expires_at` | `expired` |
| viewer accepted | `waiting_on_them` |
| otherwise | `awaiting_you` |

A proposal declined by the other side therefore still reads `waiting_on_them` (for a viewer who said yes) until
`expires_at`, then `expired`. Consent withdrawal (`close_for_consent`) closes a pending proposal as a decline by that side.

### Invariants (tests assert them)

- **I1.** `mutual` if and only if both decisions are `accept`, and `revealed_at` is set.
- **I2.** `build_revealed` succeeds if and only if the state is `mutual`.
- **I3.** Decline secrecy: cutting the history at the other side's decline changes nothing the viewer observes.
- **I4.** A never observes B's decision before the reveal.
- **I5.** `mutual`, `declined` and `expired` are terminal. Joint ideas exist only for mutual matches.
- **I6.** Withdraw is possible only from own accepted, and it leads to `proposed`.
- **I7.** At most one active pair (pending or mutual). A mutual pair is never proposed again. Other pairs wait `MATCH_COOLDOWN_DAYS`. A block in either direction forbids the pair.
- **I8.** `0 <= score <= 1`. Weighted Jaccard is symmetric.

## Reveal guard (three layers)

Identity fields (name, institution, e-mail, ORCID, OpenAlex ids) are readable only in these places:

1. **Database view** `match_reveal_v` (migration 0008). Its `WHERE mp.state = 'mutual'` and its join on non-deleted
   accounts return rows only for mutual matches, one mirrored row per side.
2. **Static test** `backend/tests/unit/matching/test_reveal_static_guard.py`. Every `FROM` or `JOIN` relation named in
   SQL under `scout/matching/adapters/` must be a `match_*` table or view, or `joint_idea`.
3. **Domain guard** in `build_revealed`, which raises `InvariantViolation("reveal_before_mutual")` for any non-mutual state.

Database guards back this up. `joint_idea` has a composite foreign key to `match_proposal(id, state)` with
`match_state = 'mutual'`, so ideas cannot be inserted for a non-mutual match, and a mutual match with ideas cannot
change state. The anonymous card reads only the proposal row.

## Profiles, candidates and proposals

- **Profile** (`matching.profile`, `ComputeResearcherProfile`). Topics come from works of the last five years
  (recency and citation weights, top 25, weights under 0.05 dropped). Methods come from the MethodExtractor, mapped to
  the controlled vocabulary (`config/methods.yaml`, version 1, 44 methods). `new_label` values go to
  `match_method_proposal` for review. Region is the continent of the country. Career band comes from the first included
  work: 7 years or fewer is early, 8 to 15 is mid, more than 15 is senior. `eligible` needs consent, a finished import,
  at least three topics and at least one method. The model is called only when `methods_hash` changes.
- **Candidates** (`CandidateQuery`). Pairs of pool members that share topics, with the exclusions below. The SQL
  returns pairs ordered by topic overlap, up to `candidate_limit` (2000) per run.
- **Exclusions.** Co-authors (`match_pair_conflict_v`, reason `coauthor`, when `MATCH_EXCLUDE_COAUTHORS`). Same
  normalised institution (reason `same_group`, when `MATCH_EXCLUDE_SAME_GROUP`). A block in either direction. A pending
  or mutual proposal. A proposal created within the cool-down.
- **Score** (`domain/scoring.py`, formula `c2`). `score = OC(topics)^alpha * (1 - WJ(methods))^beta * (0.5 + 0.5 * min(activity_a, activity_b))`,
  with `OC` the overlap coefficient `sum(min) / min(sum a, sum b)`, `WJ` the weighted Jaccard, alpha 1.0 and beta 1.5.
  Formula `c1` (`WJ` for topics too) stays available and readable for stored proposals (`ScoreBasis.formula`; ADR 017). A pair is kept only at or above `MATCH_MIN_SCORE` and only if each side has a method the
  other lacks.
- **Weekly cap** (`select_pairs`, pure). Pairs are sorted by `(-score, a, b)`. A pair is taken while both sides have
  fewer than `MATCH_MAX_OPEN_PER_WEEK` proposals created in the last seven days.
- **Write.** Each selected pair gets its rationale (MatchExplainer, or the template), then one insert with
  `ON CONFLICT DO NOTHING`. Re-running in the same ISO week is a no-op. `MatchProposed` is notified after commit.
  At most `MATCH_MAX_EXPLAINS_PER_RUN` (200) explanations are generated per run.

## Jobs

| Kind | Enqueued by | Dedupe key | Max attempts | Does |
|---|---|---|---|---|
| `matching.profile` | worker after a successful profile import (`profile_import`); `PATCH /api/v1/me/consent` | `profile:<account>` | 3 | Recomputes one profile. Without consent, deletes it. When the profile changed and is eligible, enqueues `matching.compute`. |
| `matching.compute` | `matching.profile` (payload `account_id`); the nightly job runs it inline for all | `acct:<account>` | 3 | Candidates, scoring, weekly cap, explanations, proposals. |
| `matching.reveal` | `DecideOnMatch` after a mutual accept; the nightly job for stuck ideas | `reveal:<match_id>` | 3 | Generates 1 to 3 joint ideas for a mutual match, once. |
| `matching.nightly` | `ensure_scheduled()` at worker start, and by itself | `slot:<date>` | 3 | Schedules the next slot at `MATCH_NIGHTLY_UTC_HOUR` (02:00 UTC), then: expires due proposals; closes pending proposals whose side left the pool (`close_for_consent`); re-enqueues reveals with ideas pending for over 10 minutes; recomputes each account that has a profile; runs `ComputeMatches` for all. |

Handlers are registered in `scout/worker/loop.py` (`build_handlers`). Job kind names are inline `KIND` constants in
each application module. There is no `scout/shared/jobkinds.py` in the code yet.

## Failure and retry

- **Profile.** A model error retries until the last attempt. On the last attempt the previous methods are kept, or the
  job ends `failed` when there is no previous profile.
- **Explanations.** Any error, or output that fails the guard, uses the template. The job does not fail.
- **Reveal.** `IdeasStatus` is `none`, `pending`, `ready` or `failed`. A failed reveal (no valid idea after 3 attempts)
  sets `failed`. The contact details still show.
- **Nightly.** The next slot is scheduled before any work, so one failure does not end the chain.

## Agents and prompts

| Agent | Prompt version | Tier | Feature (`CallContext`) | Called when | Fallback |
|---|---|---|---|---|---|
| MethodExtractor | 1.0.1 | fast | `matching.profile` | profile inputs changed | previous methods on the last attempt |
| MatchExplainer | 1.1.0 | fast | `matching.explain` | new proposal, within the per-run limit | vocabulary template |
| JointIdeas | 1.1.0 | reasoning | `matching.ideas` | after mutual (reveal job) | `ideas_status=failed` after 3 attempts |

Prompts are in `prompts/`. `methods_prompt_version` is `1.0.1` in `MatchConfig`.

- **Inputs.** MatchExplainer and JointIdeas get no names, institutions, years or countries. MatchExplainer gets no
  works. JointIdeas gets up to 8 work titles per side (for refs), and vault signals only when the side consented at
  reveal time. Vault text never reaches MatchExplainer.
- **Outputs.** Typed and length-bounded. Refs must belong to the correct side. Nothing is rendered as HTML.
- **Anonymity guard** (`domain/guards.py`, `leaks_identity`). Rejects years, DOIs, URLs, e-mail addresses, ORCIDs and
  OpenAlex work ids, and 6-word shingles of input titles. A failing item is dropped; a failing `why` or a perspective
  with no items uses the template.
- **Idea guard** (`check_ideas`). Drops ideas without a valid ref on both sides, and ideas that repeat 8 consecutive
  words of a vault signal.

## Endpoints

All under `/api/v1`, behind `current_account`. Source: `backend/src/scout/api/matches.py`.

| Method and path | Use case | Success | Errors |
|---|---|---|---|
| `GET /matches?cursor=&limit=` | `MatchingService.list` | 200 `MatchList` (`matching_enabled`, `items`, `next_cursor`) | none |
| `GET /matches/{match_id}` | `MatchingService.get` | 200 `MatchCard` | 404 `match_not_found` |
| `POST /matches/{match_id}/decision` | `DecideOnMatch.execute` | 200 `MatchCard` | 404; 409 `consent_required`, `match_expired`, `match_closed`, `nothing_to_withdraw`; 422 `never_similar_needs_decline` |
| `POST /matches/{match_id}/rating` | `GiveFeedback` (feedback context) | 200 feedback (`stars`, `reasons`, `comment`) | 404 when not a mutual match of the caller |

`limit` defaults to 20 (1 to 50). Lists hide proposals declined by the viewer, and expired proposals older than 30 days.
`PATCH /api/v1/me/consent` is in the identity context. It enqueues `matching.profile`.

Views. `AnonymousMatchView` (`kind=anonymous`) has the score, the field label, the other side's region and career band,
shared topic names, `you_bring`, `they_bring` (text only, no refs), `why`, `my_decision`, `created_at` and `expires_at`.
`RevealedMatchView` (`kind=revealed`) adds `contact`, `ideas_status`, `joint_ideas`, `revealed_at` and `my_rating`.
Neither view has the other side's decision, its timestamps, its rating, its profile weights or its vault signals.

## Tables and views

| Object | Migration | Purpose |
|---|---|---|
| `match_proposal` | 0008 | Proposals: decisions, state, expiry, score basis, rationale, public facets. Partial unique index on the active pair. |
| `joint_idea` | 0008 | Up to 3 ideas per mutual match. Composite FK to `match_proposal(id, state)`. |
| `match_block` | 0008 | Never-suggest pairs, both directions. |
| `match_reveal_v` | 0008 | The only path to identity fields (see the reveal guard). |
| `analytics.v_match_funnel` | 0008 | Weekly counts only: proposed, accepted, mutual, declined, expired. |
| `match_profile`, `match_profile_topic`, `match_profile_method` | 0009 | Matching profiles (topics and methods with weights; `source` is `abstract` or `vault`). |
| `match_method_proposal` | 0009 | `new_label` proposals for review. |
| `match_pool_v` | 0009 | Eligible profiles of consenting accounts in the launch field. |
| `match_pair_conflict_v` | 0009 | Account pairs that are co-authors or in the same group. Returns ids only. |
| `feedback.stars` (and its CHECK) | 0010 | 1 to 5 stars for matches. `rating` is nullable and each target type has its own CHECK. Reasons widened. |
| `analytics.v_feedback_summary` | 0010 | Recreated with `avg_stars`. |

Downgrades drop these objects and restore the earlier feedback constraints.

## Configuration

Settings are read in `scout/settings.py` and passed into `MatchConfig` in `bootstrap.py`. Variables and defaults are in
`docs-site/pages/dev/configuration.md` and `.env.example`: `MATCH_MAX_OPEN_PER_WEEK` (3), `MATCH_EXPIRY_DAYS` (14),
`MATCH_COOLDOWN_DAYS` (180), `MATCH_MIN_SCORE` (0.11, calibrated with `scripts/calibrate_matching.py`), `MATCH_ALPHA` (1.0), `MATCH_BETA` (1.5),
`MATCH_EXCLUDE_COAUTHORS` (true), `MATCH_EXCLUDE_SAME_GROUP` (true), `MATCH_MAX_EXPLAINS_PER_RUN` (200),
`MATCH_NIGHTLY_UTC_HOUR` (2), `METHODS_CONFIG` (`config/methods.yaml`). `MATCH_NON_MEMBERS` is defined but no code reads it.
Fixed in code: `candidate_limit` (2000) and `reveal_retry_minutes` (10).

## Tests

| Test | Covers |
|---|---|
| `tests/unit/matching/test_proposal.py` | Every state-machine row. |
| `tests/unit/matching/test_reveal_properties.py` | Hypothesis: I1 to I6, I3 and I4 differentially, `build_revealed` iff mutual. |
| `tests/unit/matching/test_scoring.py`, `test_profile.py`, `test_ids.py` | Scoring, weights, ids. |
| `tests/unit/matching/test_application.py` | Use cases against the fakes in `tests/fakes/matching.py`. |
| `tests/unit/matching/test_prompts.py` | Prompt front matter and versions. |
| `tests/unit/matching/test_reveal_static_guard.py` | Reveal guard, layer 2. |
| `tests/integration/test_matching_reveal_pg.py` | Reveal guard in PostgreSQL, ideas FK, active-pair index. |
| `tests/integration/test_matching_candidates_pg.py` | Candidate SQL and filters. |
| `tests/integration/test_matching_repos_pg.py`, `test_matching_api.py` | Repositories and endpoints. |

Run: `make test` (unit, no database) and `make test-integration` (needs PostgreSQL, see `scripts/dev_db.sh`).
