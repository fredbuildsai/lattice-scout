# Matching: proposals, decisions and reveal

Matching proposes a pair of researchers whose work complements each other. Each side sees an anonymous card. Names, e-mail addresses and joint ideas appear only after both sides accept. A decline is never visible to the other side.

Code and rules: [matching module](https://github.com/fredbuildsai/lattice-scout/blob/main/backend/src/scout/matching/README.md) (state machine, reveal guard, jobs, endpoints, tables). Design: `docs/design/M5-matching.md`. Decisions: `docs/DECISIONS.md` 77 to 86. This page covers the scoring, the filters, the offline fixtures and how to run the jobs locally.

## Scoring (formula `c1`)

Source: `backend/src/scout/matching/domain/scoring.py` (`score_pair`, `weighted_jaccard`).

```text
T     = WJ(topic weights of a, topic weights of b)
M     = WJ(method weights of a, method weights of b)
act   = 0.5 + 0.5 * min(activity_a, activity_b)
score = T^alpha * (1 - M)^beta * act          alpha = 1.0, beta = 1.5
```

- **Weighted Jaccard** `WJ(x, y) = sum(min) / sum(max)` over the union of keys. It is 0 when either side is empty, and it is symmetric.
- **Topics.** Works of the last five years (all dated works when there are fewer than three). Each work weighs `recency * (0.5 + 0.5 * citation_share)`, where `recency = max(0.1, 1 - 0.1 * age)`. Weights are summed per topic, normalised by the largest, cut to the top 25, and weights under 0.05 are dropped.
- **Methods.** Each mention from the MethodExtractor adds 1.0 (high confidence) or 0.5 (medium) per source ref. Totals are normalised by the largest. A method has a source of `abstract` or `vault`. The effective weight is the maximum over the permitted sources.
- **Activity.** Works in the last three years, divided by three, capped at 1.
- **Keep a pair** only if `score >= MATCH_MIN_SCORE` (0.08 by default), and each side has at least one method that the other side lacks. `ScoreBasis` keeps the numbers (`topic_overlap`, `method_overlap`, `activity`, the shared topics and each side's methods).

Worked example: the demo pair Foster and Moreira scores 0.055 in the fixture run. That is below the default 0.08. This is why the seed uses reconnect mode (see below).

## Filters

Candidates pass these checks, in the order they run:

| Filter | Where | Setting |
|---|---|---|
| Both accounts consent, have a finished profile, are eligible and are in the launch field | `match_pool_v` | `LAUNCH_FIELD`, consent |
| Not co-authors (a shared claimed OpenAlex work) | `match_pair_conflict_v`, reason `coauthor` | `MATCH_EXCLUDE_COAUTHORS` |
| Not in the same normalised institution | `match_pair_conflict_v`, reason `same_group` | `MATCH_EXCLUDE_SAME_GROUP` |
| No block in either direction (`never_similar`) | `match_block` | always |
| No pending or mutual proposal for the pair | `match_proposal` | always |
| No proposal created within the cool-down | `match_proposal.created_at` | `MATCH_COOLDOWN_DAYS` (180) |
| Score at or above the minimum, with a distinct method on each side | `score_pair` | `MATCH_MIN_SCORE` (0.08) |
| Weekly cap per person | `select_pairs` | `MATCH_MAX_OPEN_PER_WEEK` (3) |

The coauthor and same-group rules are the default **exclusions**. The seed turns both off for the demo pair (reconnect mode).

### Candidate SQL (simplified)

`SqlCandidateQuery` (`backend/src/scout/matching/adapters/sql.py`) first finds pool pairs with overlapping topics, then applies the NOT EXISTS checks. This is the shape, not the full statement:

```sql
SELECT c.a, c.b
FROM (
  SELECT x.account_id AS a, y.account_id AS b, SUM(LEAST(x.weight, y.weight)) AS inter
  FROM match_profile_topic x
  JOIN match_profile_topic y ON y.topic_id = x.topic_id AND y.account_id > x.account_id
  JOIN match_pool_v px ON px.account_id = x.account_id AND px.field = :field
  JOIN match_pool_v py ON py.account_id = y.account_id AND py.field = :field
  GROUP BY 1, 2
) c
WHERE NOT EXISTS (SELECT 1 FROM match_pair_conflict_v k WHERE k.x = c.a AND k.y = c.b AND k.reason = ANY(:reasons))
  AND NOT EXISTS (SELECT 1 FROM match_block bl WHERE (bl.account_id = c.a AND bl.blocked_account_id = c.b)
                  OR (bl.account_id = c.b AND bl.blocked_account_id = c.a))
  AND NOT EXISTS (SELECT 1 FROM match_proposal p WHERE p.a_id = c.a AND p.b_id = c.b
                  AND (p.state IN ('proposed','a_accepted','b_accepted','mutual') OR p.created_at > :cooldown))
ORDER BY c.inter DESC, c.a, c.b
LIMIT :limit;
```

The pairs are canonical (`a < b`). The join on `topic_id` uses the index `match_profile_topic_topic_idx`. The code applies one global `LIMIT` (`candidate_limit`, 2000 per run). The design note says "top 50 per person"; the code does not do that.

## Weekly cap and explanations

`select_pairs` (pure) sorts the scored pairs by `(-score, a, b)`. It takes a pair while both sides have fewer than `MATCH_MAX_OPEN_PER_WEEK` proposals created in the last seven days. The counts come from `open_counts`, which counts proposals **created** in that window, not open ones.

Each chosen pair gets a rationale first. The MatchExplainer runs outside a transaction, at most `MATCH_MAX_EXPLAINS_PER_RUN` (200) times per run. After that, or on any error, the vocabulary template is used. Then one insert per pair runs in its own transaction.

## Idempotent ids

- `match_id = uuid5(MATCHING_NS, "a:b:<ISO week>")` (`domain/ids.py`).
- `idea_id = uuid5(MATCHING_NS, "<match_id>:idea:<ord>")`. Event ids are also uuid5 over `MATCHING_NS`.
- Insert is `INSERT ... ON CONFLICT DO NOTHING ... RETURNING id`. A re-run in the same ISO week creates nothing.
- Across weeks, the candidate SQL and the partial unique index on the active pair stop duplicates while a proposal is pending or mutual. The cool-down covers declined and expired pairs.

The unit test `test_compute_creates_one_template_proposal_and_a_rerun_adds_nothing` checks the re-run.

## Offline fixtures

`scripts/make_matching_fixtures.py` regenerates the FakeLLM fixtures for the matching agents. It runs the real application in memory: profile claim and import from the recorded OpenAlex fixtures, the profile job, `ComputeMatches` in reconnect mode, two accepts and `RevealMatch`. It records every input and output.

```bash
cd backend && uv run python ../scripts/make_matching_fixtures.py          # write fixtures
cd backend && uv run python ../scripts/make_matching_fixtures.py --check  # exit 1 if stale or missing
```

Outputs, under `backend/tests/fixtures/llm/<Agent>/`: MethodExtractor for Foster and Moreira plus one edge case with no method, MatchExplainer once and JointIdeas once for the pair. Each file is named by the input hash, with the canonical input next to it (`<hash>.input.json`).

- The clock is fixed at 2026-10-21. Works, recency weights and the ISO week depend on it.
- Foster must sort before Moreira (ids `UUID(int=0xF0)` and `UUID(int=0xF1)`).
- The synthetic dev accounts have no fixture, so their explanations use the template.
- Run `--check` after you change a prompt, the vocabulary or the scoring. When the inputs no longer match a fixture, the fake LLM misses it. The explainer then falls back to the template, and the other agents fail.

## Demo data: `make seed`

`scripts/seed_dev.py` loads a local database for the demo. It refuses `APP_ENV=prod` and it is idempotent (ids come from uuid5 over `seed:<n>`).

```bash
make db        # PostgreSQL and the dev and test databases
make migrate   # migrations up to 0010
make seed      # the demo data
```

What it creates:

1. **Two real demo accounts** with matching consent and profiles built by the real jobs and the fixtures:
   - `johan.foster@example.edu` (OpenAlex A5059588273 and A5140167166),
   - `r.moreira@example.edu` (OpenAlex A5033675494).
   Both are public OpenAlex records. No private documents are created.
2. **Reconnect run** for the demo pair. Both exclusion filters are off and `MATCH_MIN_SCORE` is 0.05 for this run. Foster and Moreira share five works and an institution, so the default filters exclude them.
3. **24 synthetic researchers**, `synth01@example.edu` to `synth24@example.edu`, named "Synthetic Researcher NN (sample)". They are inserted directly with graded complementarity.
4. **Default run** of `ComputeMatches` over everyone, then it prints the login commands.

Log in as a demo user (dev auth only; the e-mail address is the account):

```bash
curl -s -X POST localhost:8000/api/v1/dev/login -H "content-type: application/json" \
  -d '{"email": "johan.foster@example.edu"}'
```

Send the returned `access_token` as `Authorization: Bearer <token>` to `GET /api/v1/matches`. Start `make api` first.

## Run the jobs locally

The worker registers the four matching handlers and schedules the nightly slot when it starts. It does not run the nightly pass at once. The next slot is 02:00 UTC (`MATCH_NIGHTLY_UTC_HOUR`).

```bash
make api      # shell 1
make worker   # shell 2
```

Flow for a new account:

1. Claim and import a profile (see [gap analysis](discovery.md) for the claim call). A finished import enqueues `matching.profile`.
2. Turn matching on: `PATCH /api/v1/me/consent` with `{"consent_matching": true}`. Vault signals are a separate switch, `consent_vault_signals`, and need matching. The call enqueues `matching.profile` again.
3. `matching.profile` computes the profile and, when it is eligible, enqueues `matching.compute` for the account.
4. `matching.compute` creates proposals. Accepting both sides enqueues `matching.reveal`, which writes the joint ideas.

To run one nightly pass now (expiry, consent closes, reveal retries, profile refresh and a full compute), call the handler directly. The same container wiring as the seed is used:

```bash
cd backend && uv run python - <<'EOF'
import asyncio

from scout.bootstrap import build_container
from scout.settings import Settings


async def main() -> None:
    c = build_container(Settings())
    try:
        print(await c.match_nightly.handle({}))
    finally:
        await c.db.dispose()


asyncio.run(main())
EOF
```

To run only the compute step, call `await c.compute_matches.execute(None)` instead.

## Configuration

Set in `.env` (see [configuration](configuration.md) for each default): `MATCH_MAX_OPEN_PER_WEEK`, `MATCH_EXPIRY_DAYS`, `MATCH_COOLDOWN_DAYS`, `MATCH_MIN_SCORE`, `MATCH_ALPHA`, `MATCH_BETA`, `MATCH_EXCLUDE_COAUTHORS`, `MATCH_EXCLUDE_SAME_GROUP`, `MATCH_MAX_EXPLAINS_PER_RUN`, `MATCH_NIGHTLY_UTC_HOUR`, `METHODS_CONFIG`. Restart the API and the worker after a change. `MATCH_NON_MEMBERS` exists but no code reads it yet.

The vocabulary is `config/methods.yaml` (version 1, 44 methods). Each entry has an `id`, a `label` and `synonyms`. A method id that is not in the file is dropped, and a new label goes to `match_method_proposal` for review.

## Tests

```bash
cd backend && uv run pytest tests/unit/matching -q                          # no database
make test-integration                                                       # needs PostgreSQL
```

The integration tests for matching are `tests/integration/test_matching_*.py`. The static reveal guard is `tests/unit/matching/test_reveal_static_guard.py`. The Hypothesis state machine is `tests/unit/matching/test_reveal_properties.py`.
