# M5 Matching: design note

Scope: plan M5 tasks 1-8, ARCHITECTURE §4.3, §6.3, §6.4, §7, §8. Decisions 77-85 in `docs/DECISIONS.md`.

## 0. Acceptance (plan M5)
* Two test accounts get a proposal. Neither sees the other's identity until both accept. After mutual, both see contact details and 1-3 joint ideas. A decline is never shown to the other side.
* No proposal between co-authors when `MATCH_EXCLUDE_COAUTHORS=true`.
* Integration test: revealed fields cannot be loaded unless `state = mutual`.

**Fixture fact that shapes the demo:** Foster and Moreira share 5 works and both resolve to "University of British Columbia". The default filters exclude them. The seed runs this pair in **reconnect mode** (both filters off), and the co-author acceptance test uses the same pair with the filter on (DECISIONS 83).

## 1. Domain: `scout/matching/domain/`
`ids.py`: `MatchId = NewType(UUID)`, `MATCHING_NS = uuid5(NAMESPACE_URL, "lattice-scout/matching")`, `match_id(a, b, iso_week) = uuid5(NS, f"{a}:{b}:{iso_week}")`, `idea_id(match, ord)`.
`enums.py`: `MatchState{proposed,a_accepted,b_accepted,mutual,declined,expired}`, `Side{a,b}`, `Decision{accept,decline}`, `Action{accept,decline,withdraw}`, `ViewerStatus{awaiting_you,waiting_on_them,mutual,declined_by_you,expired}`, `CloseReason{declined,consent_withdrawn}`, `IdeasStatus{none,pending,ready,failed}`, `Region{africa,asia,europe,north_america,south_america,oceania,unknown}`, `CareerBand{early,mid,senior,unknown}`.

`proposal.py`, `MatchProposal(AggregateRoot)` dataclass:
`id, a_id: AccountId, b_id: AccountId, field: str, score: float, score_basis: ScoreBasis, rationale: StoredRationale, a_public: PublicFacet, b_public: PublicFacet, state, a_decision: Decision|None, b_decision, a_decided_at, b_decided_at, declined_by: Side|None, close_reason: CloseReason|None, never_similar_by: Side|None, created_at, expires_at, revealed_at|None, closed_at|None, ideas_status=none`.
`PublicFacet(region, career_band)` is the only data about a person that can be read before mutual. Both facets are snapshotted at creation time, so the anonymous read path never joins identity tables.

Methods:
* `propose(...)`: requires `a_id < b_id`, `0 <= score <= 1`, and `expires_at = now + MATCH_EXPIRY_DAYS`.
* `side_of(viewer)`: raises `MatchNotFound` (404) for a non-participant.
* `decide(viewer, action, now, never_similar=False) -> list[DomainEvent]`. The table below is the whole state machine. `own` is the viewer's side and `other` is the other side.

| state before | accept | decline | withdraw |
|---|---|---|---|
| any pending state, `now >= expires_at` | `expire()`, then 409 `match_expired` | same | same |
| proposed | `own_accepted` | `declined` (declined_by=own) | 409 `nothing_to_withdraw` |
| own_accepted | no-op | `declined` | `proposed` (own decision cleared) |
| other_accepted | `mutual`, revealed_at=now, event `MatchBecameMutual` | `declined` | 409 `nothing_to_withdraw` |
| mutual | no-op | 409 `match_closed` | 409 `match_closed` |
| declined, declined_by=own | 409 `match_closed` | no-op | 409 `match_closed` |
| declined, declined_by=other | **record own decision silently, return OK** (accept and withdraw toggle own decision; a decline sets it) | same | same |
| expired | 409 `match_expired` | same | same |

* `never_similar=True` is only valid with decline (otherwise 422). It sets `never_similar_by` and the use case writes a `match_block` row. It is also honoured on a silently recorded decline.
* `expire(now) -> bool`: moves a pending state to `expired` once `now >= expires_at`.
* `close_for_consent(side, now)`: moves a pending state to `declined` with `declined_by=side` and `close_reason=consent_withdrawn`. The other side sees exactly what it sees after a decline.
* `status_for(viewer, now) -> ViewerStatus`, a **pure masking function**:
  `mutual` gives MUTUAL. Declined by me gives DECLINED_BY_YOU. `expired`, or `now >= expires_at`, gives EXPIRED. Otherwise my own decision decides: accept gives WAITING_ON_THEM, decline gives DECLINED_BY_YOU, none gives AWAITING_YOU. **The other side's decision is never an input**, except through `mutual`.

`reveal.py`:
* `reveal_allowed(state) -> bool` returns `state is MUTUAL`.
* `build_anonymous(p, viewer, now, field_label) -> AnonymousMatchView`.
* `build_revealed(p, viewer, contact, ideas) -> RevealedMatchView` raises `InvariantViolation("reveal_before_mutual")` unless `reveal_allowed`.
* View DTOs are frozen Pydantic models in the domain (§6). They are not SQLModel.

Events: `MatchProposed(match_id, a_id, b_id)`, `MatchBecameMutual(match_id, a_id, b_id)`, `JointIdeasReady(match_id)`. **There is no decline event**, so nothing downstream can leak a decline.

### Invariants (tests assert them)
I1. `state == mutual` ⇔ both decisions are `accept` ⇔ `revealed_at` is set.
I2. `build_revealed` succeeds ⇔ `state == mutual`. Contact is only loadable for mutual (DB level, §2).
I3. Decline secrecy. Take an action history S, and cut it at B's first decline (or at a consent close for B) to get S'. For every point in time, A observes the same results under S and S': every `status_for(A, t)` and every response or error code to A's own actions.
I4. A never observes B's decision. Changing B's accept and withdraw history before mutual changes nothing A observes, except the reveal itself.
I5. `mutual`, `declined` and `expired` are terminal. `mutual` is never left. Joint ideas exist only for mutual matches.
I6. Withdraw is possible only from `own_accepted`, and it leads to `proposed`.
I7. At most one active (pending or mutual) proposal per unordered pair. A mutual pair is never proposed again. Other pairs wait 180 days after the last `created_at`. A `match_block` in either direction prevents the pair forever.
I8. `0 <= score <= 1`. Weighted Jaccard is symmetric, and J(x,x)=1 for x non-empty. J(empty, anything) = 0.

## 2. Persistence: migration `20261022_0008_matching.py` (revision 0008)
* `match_profile(account_id uuid PK → account ON DELETE CASCADE, researcher_id uuid → researcher CASCADE, field text, region text, career_band text, activity real CHECK 0..1, eligible bool, topics_hash text, methods_hash text, vocabulary_version text, methods_prompt_version text, uses_vault_signals bool, computed_at, created_at, updated_at)`.
* `match_profile_topic(account_id → match_profile CASCADE, topic_id text, name text, weight real CHECK (0,1], PK(account_id, topic_id))` plus an index on `(topic_id) INCLUDE (account_id, weight)`.
* `match_profile_method(account_id, method_id text, weight real CHECK (0,1], source text CHECK IN ('abstract','vault'), PK(account_id, method_id, source))`.
* `method_label_proposal(id, account_id CASCADE, label text, source_refs text[], created_at, UNIQUE(account_id, lower(label)))` holds the `new_label` proposals for review.
* `match_proposal`: the columns of §1 plus `score_basis jsonb, rationale jsonb, a_public jsonb, b_public jsonb, ideas_prompt_version text, updated_at`.
  Constraints:
  * CHECKs: `a_id < b_id`; state IN (...); decisions IN ('accept','decline'); `(state='mutual') = (revealed_at IS NOT NULL)`; `state<>'mutual' OR (a_decision='accept' AND b_decision='accept')`; `state<>'declined' OR declined_by IS NOT NULL`.
  * `UNIQUE(id, state)`, used by the FK below.
  * Partial unique index `(a_id, b_id) WHERE state IN ('proposed','a_accepted','b_accepted','mutual')`.
  * Indexes: `(a_id, created_at)`, `(b_id, created_at)`, and `(expires_at) WHERE state IN pending`.
* `joint_idea(id uuid PK, match_id uuid, match_state text NOT NULL DEFAULT 'mutual' CHECK (match_state='mutual'), ord smallint CHECK 0..2, title, question, uses_from_a jsonb, uses_from_b jsonb, first_step, possible_output, prompt_version, model, created_at, UNIQUE(match_id, ord), FOREIGN KEY (match_id, match_state) REFERENCES match_proposal(id, state) ON DELETE CASCADE)`.
  **DB guarantee:** an idea cannot be inserted for a non-mutual match, and a mutual match with ideas can never change state.
* `match_block(account_id, blocked_account_id, match_id, created_at, PK(account_id, blocked_account_id))`, both columns referencing account with CASCADE.
* View **`match_reveal_v`**, the only path to identity fields:
  ```sql
  SELECT mp.id AS match_id, v.viewer_id, v.other_id, r.display_name, r.institution, r.orcid, acc.email,
         ARRAY(SELECT x.openalex_author_id FROM researcher_openalex_id x WHERE x.researcher_id = r.id ORDER BY x.position) AS openalex_ids
  FROM match_proposal mp
  CROSS JOIN LATERAL (VALUES (mp.a_id, mp.b_id), (mp.b_id, mp.a_id)) v(viewer_id, other_id)
  JOIN account acc ON acc.id = v.other_id AND acc.deleted_at IS NULL
  JOIN researcher r ON r.account_id = v.other_id
  WHERE mp.state = 'mutual';
  ```
* View `match_pool_v(account_id, field)`: `match_profile` with `eligible` set, joined to `account` where `consent_matching AND deleted_at IS NULL` (`field = account.launch_field`).
* View `match_pair_conflict_v(x, y, reason)`, built as a UNION ALL of two parts:
  * `coauthor`: two researchers claimed the same `claimed_work.openalex_work_id`, whether included or not.
  * `same_group`: `lower(btrim(institution))` is equal and not null.
  The view returns account ids only, never the institution.
* `feedback` changes (match ratings live in the feedback context):
  * Add `stars smallint CHECK 1..5`. Make `rating` nullable.
  * CHECK `(target_type='opportunity' AND rating IS NOT NULL AND stars IS NULL) OR (target_type='match' AND stars IS NOT NULL AND rating IS NULL)`.
  * Widen the reasons CHECK with `methods_fit, right_seniority, shared_timeline, too_similar, already_knew_them` (`wrong_field` is reused).
* `analytics.v_match_funnel(week, proposed, accepted_any, mutual, declined, expired)`: counts only.
* `researcher_method` (0003) stays unused and is marked deprecated in the model docstring.
* Downgrade drops everything above and restores the feedback CHECKs.

**Reveal guard, three layers:** (1) the `match_reveal_v` WHERE clause; (2) a static test that every `FROM|JOIN <relation>` in `scout/matching/adapters/**/*.py` names a `match_*` table or view, or `joint_idea`; (3) the domain guard in `build_revealed`. A DB role with column grants was rejected: one app role, more ops cost.

## 3. Researcher profiles (`matching.profile` job, `ComputeResearcherProfile`)
Inputs come through the ACL `scout/matching/application/sources.py`, the only matching module allowed to import `ProfileService`, `VaultService` and `IdentityService` (new import-linter contract, like DECISIONS 68). The ports are `ProfileSource.matching_input(account) -> ProfileInput|None` (works: ref, title, abstract, year, cited_by, topics; country; import_done; researcher_id), `ConsentSource.get(account) -> Consent(matching, vault_signals, field)` and `VaultSignalSource.signals(account, sections) -> list[SignalItem]`. Signals are verified insight items only, `ref="insight:<item_id>"`, at most 10, text at most 300 chars with emails, URLs and phone numbers stripped.

Pure functions in `domain/profile.py`:
* **Topics:** take included works with `year > now.year-5` (all included works when there are fewer than 3). Recency `r = 1 - 0.1*(now.year - y)`. Citation share `c = cited_by / max cited_by in the window` (0 if the max is 0). `w_work = r * (0.5 + 0.5c)`. `topic += w_work * (score or 1.0)`. Normalise by the max, keep the top 25, drop weights under 0.05.
* **Methods:** call MethodExtractor on at most 20 newest included abstracts (1500 chars each), plus signals from the `methods` section when `consent_vault_signals` is set.
  * Keep `method_id` only when it is in the vocabulary. Drop refs that are not in the input, and drop mentions left without refs.
  * `weight = Σ_refs (1.0 high | 0.5 medium)`, normalised by the max. The source is `abstract` or `vault` per ref. Effective methods are the max over the permitted sources.
  * Each `new_label` becomes a `method_label_proposal`.
* `region` maps the country (ISO-2 to continent) through a dict in `domain/regions.py`.
* `career_band` uses years since the first included work: 7 or fewer is early, 8-15 mid, more than 15 senior, no works unknown.
* `activity = min(1, included works in the last 3 years / 3)`.
* `eligible = consent_matching AND import_done AND ≥3 topics AND ≥1 method`.
* `methods_hash = sha256(sorted (ref, sha(abstract)) + signal refs/text hashes + vocabulary_version + prompt_version)`. The LLM is called **only when it changes**.

Consent:
* No `consent_matching`: delete `match_profile`. The account leaves the pool, and the nightly sweep closes its pending proposals.
* Vault signals withdrawn: the hash changes and the next run drops the `vault` method rows.

Triggers (kind string `MATCHING_PROFILE = "matching.profile"` in `scout/shared/jobkinds.py`):
* `ImportProfile` success enqueues it, with `dedupe_key = account_id`.
* `IdentityService.update_consent` gets an optional `jobs: JobQueuePort` and enqueues it.
* The nightly job refreshes everyone.

If the profile changed, the job enqueues `matching.compute {account_id}`. On an LLM error the job retries. On the last attempt it keeps the previous methods.

`config/methods.yaml` (version 1, field `bio-based-materials`): entries `{id, label, synonyms[]}`. The loader lives in `matching/adapters/vocabulary.py`; the domain gets `Vocabulary(version, terms)`. Seed ids (44):
`lca, tea, ftir, raman, nmr, xrd, saxs_sans, sem, tem, afm, tga, dsc, dma, rheology, mechanical_testing, gpc_sec, gc_ms, hplc_lcms, bet_porosimetry, contact_angle, cone_calorimetry, reactive_extrusion, moulding, electrospinning, freeze_casting, foaming, additive_manufacturing, nanocellulose_isolation, lignin_fractionation, enzymatic_hydrolysis, fermentation, pyrolysis_htl, chemical_modification, polymer_synthesis, membrane_fabrication, adsorption_testing, photocatalysis, electrochemistry, biodegradation_testing, toxicity_testing, molecular_simulation, process_modelling, machine_learning, design_of_experiments`.

## 4. `ComputeMatches` (`matching.compute`)
Payload is `{account_id?}`. With an account it computes only pairs involving that account. Dedupe key: `acct:<id>`, or `all:<date>`.

1. SQL candidates in `CandidateQuery.candidates(field, source, per_source=50)`. Self-join `match_profile_topic` on `topic_id`, with both sides in `match_pool_v`. Compute `inter = Σ LEAST(wa, wb)`, rank per x, keep the top 50. Then exclude:
   * pairs in `match_pair_conflict_v` whose reason is enabled (`coauthor` when `MATCH_EXCLUDE_COAUTHORS`, `same_group` when `MATCH_EXCLUDE_SAME_GROUP`, default true);
   * pairs with a `match_block` in either direction;
   * pairs with a pending or mutual proposal;
   * pairs whose last proposal has `created_at > now - MATCH_COOLDOWN_DAYS` (180).
   It returns canonical pairs (`a < b`).
2. Score (pure, `domain/scoring.py`): `T = WJ(topics)`, `M = WJ(methods)`, `act = 0.5 + 0.5 * min(act_a, act_b)`, `score = T^α * (1-M)^β * act` with α=1 and β=1.5 (configurable). Keep a pair only if `score >= MATCH_MIN_SCORE` (0.08) and both `a_only` and `b_only` method sets are non-empty. `ScoreBasis` holds `topic_overlap, method_overlap, activity, alpha, beta, shared_topics[≤5 names], a_only[≤5 labels], b_only[≤5 labels], formula="c1"`.
3. Weekly cap (pure `select_pairs`): sort by `(-score, a, b)` and take pairs greedily while both sides have `created in the last 7 days < MATCH_MAX_OPEN_PER_WEEK`. The counts come from SQL first.
4. For each selected pair, call MatchExplainer outside a transaction (§5). Then one transaction per pair: `insert_new` with `ON CONFLICT DO NOTHING` on the id or the active-pair index. Id = `uuid5(a, b, iso_week(now))`. Re-running the job in the same week is a no-op. Emit `MatchProposed`, then call the notifier after commit.

The result is `{"candidates", "proposed", "skipped_conflict"}`. At most `MATCH_MAX_EXPLAINS_PER_RUN` (200) LLM explanations run per job. After that the template is used.

`matching.nightly`: self-rescheduling with dedupe `slot:<date>` at `MATCH_NIGHTLY_UTC_HOUR` (2), like `discovery.sweep`. It schedules the next slot first, then:
* (a) expire due proposals;
* (b) `close_for_consent` for pending proposals whose party left the pool;
* (c) re-enqueue `matching.reveal` for mutual matches whose `ideas_status = pending` is older than 10 minutes;
* (d) recompute each consenting profile inline (hash-gated, so cheap);
* (e) run `ComputeMatches(all)` inline.
The worker calls `ensure_scheduled()` at start.

## 5. Agents (`matching/ports/agents.py` `MatchAgentsPort`, adapter `AgentsMatching` via `AgentFactory`)
`AgentOutcome` moves to `scout/ports/llm.py`; discovery re-exports it. I/O models live in `matching/domain/agent_io.py`.
* `extract_methods(MethodExtractorInput{vocabulary: list[{method_id,label,synonyms}], sources: list[{source_ref,text}]}) -> MethodSet{methods: list[MethodMention{method_id|None, new_label|None, source_refs, confidence: high|medium}] ≤25}`.
* `explain(MatchExplainerInput{a: ResearcherSummary, b: ResearcherSummary, score_breakdown{shared_topics, a_only_methods, b_only_methods, score}}) -> MatchRationale{for_a: Perspective, for_b: Perspective}`.
  * `ResearcherSummary{topics ≤10 {id,name,weight}, methods ≤10 {id,label}, works ≤8 {ref:"work:W…", title ≤200}}`. There are **no names, institutions, years, countries or career data**. Works are the side's newest window works that carry its distinct methods or shared topics.
  * `Perspective{you_bring ≤3, they_bring ≤3: BringItem{text ≤120, source_refs ≤3}, why ≤300}`.
* `joint_ideas(JointIdeasInput{a, b: ResearcherSummary, a_signals, b_signals: list[SignalItem] ≤10, shared_topics, a_only_methods, b_only_methods}) -> JointIdeaList{ideas: 1..3 JointIdea{title ≤100, question ≤250, uses_from_a, uses_from_b: 1..3 BringItem, first_step ≤200, possible_output ≤100}}`. There are no display names, which differs from prompt 1.0.0. Signals are included only for a side whose consent is checked **at reveal time**.

Validation in the application, with pure helpers in `domain/guards.py`:
* Refs must be among the input refs of the correct side.
* **Anonymity guard** on MatchExplainer text: reject 6-word shingles of any input title, `\b(19|20)\d{2}\b`, DOI, URL, email, ORCID and `W\d{5,}` patterns. A failing item is dropped. A failing `why`, or a perspective left with an empty list, is replaced by the **template**: method labels and topic names from the controlled vocabulary.
* An LLM error of any kind (including `FakeFixtureMissing`) also falls back to the template. The `rationale.source` value is `llm` or `template`.
* JointIdeas: drop an idea that has no valid ref on either side or contains an 8-word shingle of any signal. Zero ideas raises `LLMOutputInvalid`, which retries the job (`max_attempts=3`). The last attempt sets `ideas_status=failed`, and the contacts still show.

Prompt-injection stance:
* Titles, abstracts and insights are data. No agent has tools.
* Outputs are typed, length-bounded, ref-checked and guard-checked, and nothing is rendered as HTML.
* Vault text never reaches MatchExplainer. It reaches only MethodExtractor (own data, consent) and JointIdeas (consent).
* Traces redact inputs (existing factory setting).

Cost per call:
* MethodExtractor: fast tier, ≤ ~9k input tokens, one call per profile change.
* MatchExplainer: fast tier, ≤ 1 call per new proposal (≤ 3 per user per week).
* JointIdeas: reasoning tier, ≤ 3 attempts per mutual match.

The `CallContext` feature is `matching.profile`, `matching.explain` or `matching.ideas`.

Fixtures:
* The prompt-engineer writes `scripts/make_matching_fixtures.py`, modelled on `make_discovery_fixtures.py`, with the clock fixed at 2026-10-21. It produces `MethodExtractor` fixtures ×2 (Foster, Moreira), `MatchExplainer` ×1 and `JointIdeas` ×1 for that pair.
* Synthetic pairs use the template, because no fixture exists for them.
* Unit tests use `FakeLLM.script` callables.
* Prompts are bumped to `MatchExplainer 1.1.0` and `JointIdeas 1.1.0` (inputs without names/region/band), and `MethodExtractor 1.0.1` (vocabulary file).

## 6. Use cases, read models and endpoints
`MatchingService` in `application/service.py`:
* `list(viewer, cursor) -> MatchList`. Hidden: `declined_by_you`, and `expired` older than 30 days. Paging uses an offset cursor, newest first.
* `get(viewer, id) -> MatchCard`. The answer is 404 for unknown, not mine, or hidden.
* `DecideOnMatch.execute(viewer, id, action, never_similar) -> MatchCard`:
  * The viewer must have `consent_matching`, otherwise 409 `consent_required`.
  * One transaction: `get_for_update`, `decide`, the block row, `save`, and the audit `match.mutual` on mutual.
  * After commit: enqueue `matching.reveal` (dedupe on the match id; the nightly step (c) catches a lost enqueue) and the notifier.
* `RevealMatch.handle({match_id})` (`matching.reveal`):
  * If the match is not mutual, return `{skipped}`; ideas are **never** generated before mutual. If `ideas_status=ready`, return.
  * Build inputs, call JointIdeas, validate.
  * In one transaction: lock, re-check mutual, insert ideas with uuid5 ids (`ON CONFLICT DO NOTHING`), set `ideas_status=ready`.
* `RateMatch`: the endpoint calls feedback `GiveFeedback` with `stars`. The feedback target port checks for a mutual match that the author takes part in, otherwise 404.

DTOs (`MatchCard = Annotated[AnonymousMatchView | RevealedMatchView, Field(discriminator="kind")]`):
* `AnonymousMatchView{kind:"anonymous", id, status: ViewerStatus, score, headline, field_label, region, career_band, shared_topics: list[str] ≤5, you_bring: list[BringItem], they_bring: list[BringText{text}], why, my_decision: accept|decline|None, created_at, expires_at}`.
  * `headline` = the label of the top `b_only` method, with the top shared topic as a fallback.
  * `they_bring` has **no refs**: refs point to the other person's papers and would identify them.
* `RevealedMatchView{kind:"revealed", id, status:"mutual", score, shared_topics, you_bring, they_bring: list[BringItem], why, contact: Contact{display_name, institution|None, email, orcid|None, openalex_ids: list[str], region, career_band}, ideas_status, joint_ideas: list[JointIdeaView{title, question, uses_from_you, uses_from_them, first_step, possible_output}], revealed_at, my_rating: int|None}`.
* `MatchList{matching_enabled: bool, items: list[MatchCard], next_cursor|None}`.

These fields are **never** sent to either side: the other side's decision, its `decided_at`, `declined_by`, `close_reason`, `never_similar_by`, `updated_at`, `closed_at`, the other side's rating, its profile weights, and its vault signals verbatim.

Endpoints (`api/matches.py`, OpenAPI summaries, problem+json):
* `GET /matches?cursor=` returns `MatchList`.
* `GET /matches/{id}` returns `MatchCard`.
* `POST /matches/{id}/decision {action: accept|decline|withdraw, never_similar: bool=false}` returns `MatchCard`. Errors: 404, 409 `match_expired`/`match_closed`/`nothing_to_withdraw`/`consent_required`, 422.
* `POST /matches/{id}/rating {stars:1..5, reasons: list[MatchReason], comment ≤1000}` returns `{stars, reasons, comment}`.
* New analytics catalogue events: `match_viewed`, `match_decided` (the action, never the other side's), `match_rated`.

## 7. Notifications
M6 owns them. M5 adds `matching/ports/notifier.py` `MatchNotifier{async proposed(account_id, match_id); async mutual(account_id, match_id)}`:
* The adapter is `LoggingMatchNotifier`; the fake records calls.
* Calls are best effort after commit and never raise into the use case.
* There is no decline method, by design.

## 8. Test plan
* **Unit, Hypothesis (test-engineer first)**: `RuleBasedStateMachine` over `decide(side, action, never_similar)`, `advance(days 0..30)`, `expire`, `close_for_consent(side)`. It checks I1, I5 and I6 after every step, plus state = derive(decisions, declined_by, expired). A differential test checks I3 and I4: run S and S' and compare A's observation log. Generated pairs and weights check I8. `select_pairs` never exceeds the cap and is deterministic. Topic and method weights stay in [0,1].
* **Unit (fakes)**:
  * ComputeResearcherProfile: hash gate, no LLM on an unchanged hash, consent off deletes the profile, vault rows only with consent.
  * ComputeMatches: filters, cap, idempotent re-run, template fallback, anonymity guard drops a title leak.
  * DecideOnMatch: every table row in §1, the block row, reveal enqueued once.
  * RevealMatch: skip non-mutual, refs and verbatim guard, last-attempt failure.
  * Views: `build_revealed` raises for each non-mutual state.
* **Integration (Postgres)**:
  * (a) **Unloadable revealed fields:** for each non-mutual state, `RevealRepo.contact()` returns None and raw `SELECT` on `match_reveal_v` returns 0 rows. Mutual gives 2 mirrored rows. A deleted account gives 0.
  * (b) A `joint_idea` insert on a non-mutual match raises IntegrityError, and so does updating a mutual match's state.
  * (c) The active-pair unique index.
  * (d) Candidate SQL plus filters on the Foster/Moreira fixtures: `MATCH_EXCLUDE_COAUTHORS=true` gives 0 proposals, reconnect mode gives 1.
  * (e) The feedback `stars` CHECK.
* **Static**: the adapter relation-name test (§2), and the import-linter contract for the ACL.
* **API/e2e**: two accounts go proposal → accept/accept → reveal job → contact plus 1-3 ideas. A decline leaves the other side at `waiting_on_them` and then `expired`.

`scripts/seed_dev.py` (`make seed`):
* Refuses `APP_ENV=prod`. Idempotent: uuid5 over `seed:<n>`.
* (1) Foster and Moreira dev accounts (`johan.foster@example.edu`, `r.moreira@example.edu`): claim from fixtures, import, consent on, profiles through the MethodExtractor fixtures.
* (2) 24 synthetic accounts `synth01..24@example.edu`, display name "Synthetic Researcher NN (sample)", institution "Example University N" (6 values), countries across 5 continents. The script inserts `researcher` and `match_profile*` rows directly, using fields.yaml topics and vocabulary methods arranged into graded complementarity.
* (3) `ComputeMatches(all)` with defaults, plus one run in reconnect mode for the demo pair.
* (4) Prints the `POST /dev/login` commands.

## 9. Slices (each ends green and pushable)
**A, domain + DB:**
1. Enums, ids, proposal, reveal, views, scoring, regions, guards (pure).
2. Hypothesis tests (test-engineer).
3. Migration 0008.
4. SQL adapters: `MatchRepo`, `RevealRepo` (view only), `JointIdeaRepo`, `BlockRepo`, `MatchProfileRepo`, `CandidateQuery`.
5. Fakes in `tests/fakes/matching.py`.
6. Integration tests (a-c, e) and the static test.
7. Import-linter ACL contract.

**B, application + agents + API:**
1. `config/methods.yaml`, the vocabulary loader, agent_io.
2. Prompt-engineer: prompts plus `make_matching_fixtures.py`.
3. `AgentOutcome` move.
4. ACL sources.
5. ComputeResearcherProfile, ComputeMatches, nightly.
6. Triggers in profile and identity.
7. Decide, Reveal, queries, notifier.
8. Feedback `stars`.
9. `api/matches.py`, worker handlers, bootstrap, settings plus `.env.example`.
10. Integration test (d), API tests, `make gen-client`.

**C, seed + UI + docs:**
1. `seed_dev.py`, `make seed`.
2. frontend-dev builds W7, M7 and M8 (list, anonymous card, waiting state, reveal with contact and ideas, star rating with chips, empty states), plus e2e.
3. doc-writer: README matching and user notes "Matching". Then the reviewer.

## 10. Open questions (defaults taken, none blocking)
Q1. "Same group" means the same normalised institution, on by default (no group data in OpenAlex).
Q2. B is not told that A said yes.
Q3. A mutual reveal survives a later consent withdrawal and disappears with account deletion.
Q4. The "count of joint ideas" on the anonymous card is dropped: ideas exist only after mutual (§4.3 I3 wins over §6.3).
Q5. The UI shows the score as a percentage (`round(score*100)`). The calibration is tuned in M7.
Q6. A decline is final for that proposal. The pair can return after the cool-down unless blocked.
Q7. The decision body replaces §8 `{accept}` with `{action}` so that withdraw has a verb.
Q8. Match rating uses 1-5 stars in `feedback.stars`, while opportunities keep up/down.
