# Operations: signals, alerts, dashboards, retention (M6)

All numbers come from the `analytics` schema, which the application role can read and PostgREST roles cannot.

## Views

| View | Question it answers |
|---|---|
| `v_funnel_discovery` | Per week: signed up, claimed a profile, started a run, finished a run, rated an opportunity (distinct accounts). |
| `v_funnel_matching` | Per week: proposed, accepted by at least one side, mutual, declined, expired, rated (match stars). |
| `v_run_cost` | Cost, calls and duration per run. |
| `v_llm_usage_daily`, `v_feature_usage`, `v_page_views_daily`, `v_page_transitions`, `v_session_paths` | Usage and cost by day, feature and page. |
| `v_feedback_summary`, `v_claim_quality`, `v_match_funnel` | Feedback, claim quality and the raw match funnel. |
| `v_ops_signals` | One row for alerts: dead jobs in 24 h, age of the oldest due job, failed runs in 24 h, LLM error rate in the last hour, LLM and OpenAlex spend today. |

## Alert rules (to configure where the logs and the database are watched)

| Alert | Condition | Source |
|---|---|---|
| Budget warning / cap reached | log event `budget.warning` or `budget.cap_reached` | application log |
| Job failures | `dead_jobs_24h > 0` | `v_ops_signals` |
| Queue stuck | `oldest_due_job_age_s > 600` | `v_ops_signals` |
| Run failures | `failed_runs_24h >= 3` | `v_ops_signals` |
| LLM errors | `llm_error_rate_1h > 0.1` | `v_ops_signals` |
| Erasure stuck | log event `identity.auth_delete_failed` | application log |
| API errors | 5xx rate above 2% over 5 minutes | request log (`status`) |

Budget events: `budget.warning` and `budget.cap_reached` carry `service` = `llm`, `openalex` or `llm_calls`. `llm_calls` counts provider requests per UTC day against `GLOBAL_DAILY_LLM_CALLS` (free models cost 0 USD, so only the counter can trip). Set it below the providers' combined free daily quota. 

Confidential-data events (ADR 016; none carries content): `confidential_data_disabled` (warning at start: no deployment is authorized, so analyses, matching and document processing use published data only), `confidential_allow_any` (start; error level in production: `LLM_CONFIDENTIAL_ALLOW_ANY_PROVIDER` sends private text to every provider, use it for development only), `agent_skipped reason=no_published_form` (a wholly confidential step was not sent), `confidential_refused` (backstop in the adapter; users never see it), `vault.awaiting_approved_provider` (a document was parked) and the vault sweep result `awaiting_requeued`. Router warnings carry route, deployment, outcome and exception class only. To authorize a provider see BLOCKERS B6.

## Live model eval (free models)

Tests whether real free models can carry Scout's agents: strict structured output, the Critic and evidence rules, and the anonymity guards. Built for OpenRouter (Nemotron `openrouter-nemotron-super`, GLM), Groq (`groq-gpt-oss`) and Mistral (`mistral-small`, `mistral-large`) from `config/llm_router.yaml`. Code: `eval/live_eval/`; thresholds: `eval/thresholds.yaml`. It never runs in `make check` or on push; offline it is tested against a scripted fake model (`backend/tests/unit/live_harness`).

**Run it** (a networked machine with at least one key; `llmrouter-free` installed, see BLOCKERS B5):

```bash
export OPENROUTER_API_KEY=...   # and/or GROQ_API_KEY, MISTRAL_API_KEY (never commit them, never paste them in logs)
cd backend
uv run python ../eval/run_eval.py --live --deployment openrouter-nemotron-super            # published-class agents only
uv run python ../eval/run_eval.py --live --deployment openrouter-nemotron-super,groq-gpt-oss,mistral-small \
    --allow-synthetic-confidential openrouter-nemotron-super --max-calls 60 --runs 2        # + synthetic sample inputs
uv run python ../eval/run_eval.py --live --route reasoning --agents Critic,GapAnalyst        # a route, some agents
uv run python ../eval/run_eval.py --live --dry-run                                           # no network: scripted fake model
```

Or start the manual workflow `live-eval.yml` (type `RUN`; the keys come from repository secrets).

| Flag | Meaning |
|---|---|
| `--deployment A,B` / `--route R` | Which deployments to score, one at a time, each pinned alone on both routes so a fall-over cannot hide a weak model. One of the two is required. A deployment whose key variable is unset is skipped with a note. |
| `--max-calls N` | Hard cap on provider requests **per deployment**, router retries included (default 60, ceiling 500). The full case set is 51 cases per run, so 60 fits one run with a few retries. The check happens between cases, so one case can overshoot by the router's own retries. |
| `--runs N` | Repeat every case N times (default 1). Counts against `--max-calls`. |
| `--agents A,B` | Subset of agents. Cases are interleaved by agent, so a small cap still samples every agent. |
| `--allow-synthetic-confidential D1,D2` | Deployments that may receive the synthetic sample inputs of the confidential-class agents. Default none: those agents are skipped and the verdict can at best be `GO_PUBLISHED_ONLY`. |
| `--out PATH` | Report prefix; writes `PATH.json` and `PATH.md` (default `eval/reports/live-<date>`). The JSON holds metrics and per-case failure codes, never prompts or model text. |
| `--thresholds`, `--config` | Other thresholds or router YAML. |

**Data rules.** Published-class agents (`ClusterLabeler`, `MatchExplainer`) run on recorded inputs made of public OpenAlex data (Foster, Moreira) and are sent to every listed deployment. Confidential-class agents (`GapAnalyst`, `Critic`, `QueryPlanner`, `MethodExtractor`, `InsightExtractor`, `InsightTopicTagger`, `JointIdeas`) never see recorded vault text: they run on synthetic inputs built from public work titles and fictional abstracts and lab notes that start with `[SAMPLE]` (`eval/live_eval/samples.py`). Synthetic text is not confidential, so it may go to a free provider, but only to deployments you list in `--allow-synthetic-confidential`. The runner refuses any confidential-class case that is not marked synthetic. Do not add real documents to the cases.

**What is measured** per deployment and agent: schema-valid rate (JSON parse plus Pydantic), strict-schema acceptance (the adapter sends the JSON schema as `response_format`; if the provider rejects it, as Groq can, the harness switches that deployment to JSON mode, retries the case and reports `schema_rejections` and `fallback_ok_rate`), empty-output rate, latency p50 and p95, retries, 429 and quota events, unavailable rate. Rule checks: the Critic against 8 planted-bad drafts (an unsupported claim next to a good one: overreach, wrong number, causation, contradiction, scale, injection in an abstract) and 8 good drafts (`bad_catch_rate`, `good_keep_rate`); GapAnalyst and InsightExtractor quotes verbatim in their source (`quote_verbatim_rate`), at least two evidence items per opportunity; invented ids and refs (`invented_id_count`, must be 0); MatchExplainer anonymity over 12 cases whose works carry public titles (year, id, DOI, URL, e-mail or 6 title words); JointIdeas anonymity plus copied-signal check over 4 cases; MethodExtractor recall of expected methods.

**Verdict per deployment** (`eval/thresholds.yaml`): `GO` all required agents pass; `GO_PUBLISHED_ONLY` the published agents pass and a required confidential-class agent was not evaluated (the pilot runs on published data only, DECISIONS 129); `NO_GO` a required agent fails (the reasons list the failing rules); `INCOMPLETE` too few completed calls. Exit code 0 for GO and GO_PUBLISHED_ONLY, 1 otherwise, 2 for usage errors or when nothing ran. With 2 to 16 cases per agent one miss moves a rate a lot: read the numbers, repeat with `--runs 2` or 3 before deciding, and put failures into `docs/prompt-findings/` (tuning loop in its README).

**Cost and duration.** Cost is zero: only free deployments are accepted (OpenRouter models must end in `:free`, `LLM_ROUTER_ALLOW_PAID` is off and the router's paid budget is 0). The limit is the free quota (Groq `rpm: 30`, Mistral `rpm: 20`, OpenRouter free models have a daily request limit): the harness paces calls to the deployment's `rpm` and counts every request. Rough estimate, not measured yet: 5 to 20 minutes per deployment for one run of 51 cases, dominated by the rpm pacing and slow reasoning-tier calls. A 429 on the daily quota shows as `quota_events` and the rest of that deployment's cases are `unavailable`: rerun the next UTC day (BACKLOG F-49).

## Dashboards (Metabase or similar)

Connect a read-only database user to the `analytics` schema. Suggested first board: weekly discovery funnel, weekly matching funnel, daily cost against the two global caps, feedback summary, `v_ops_signals` as number tiles. Pilot success metrics (M7): share of opportunities rated useful, share of proposals accepted, share of mutual matches with contact.

## Retention and opt-out

- Raw rows of `analytics_event`, `llm_call` and `external_call` live in monthly partitions. The daily partition job drops partitions older than `ANALYTICS_RETENTION_MONTHS` (13) and creates the next ones (each with RLS).
- A user can switch analytics off (`analytics_opt_out` in `PATCH /api/v1/me/notifications`). `POST /events` then stores nothing for that account; the decision is cached for up to a minute.
- Account erasure removes the user's events and sessions and anonymises cost rows (see the deletion page).
