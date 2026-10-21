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

Budget events: `budget.warning` and `budget.cap_reached` carry `service` = `llm`, `openalex` or `llm_calls`. `llm_calls` counts provider requests per UTC day against `GLOBAL_DAILY_LLM_CALLS` (free models cost 0 USD, so only the counter can trip). Set it below the providers' combined free daily quota. A refused vault call logs `vault_data_refused agent=... route=...` without any content: it means no sanctioned deployment is configured for private data (ADR 015).

## Dashboards (Metabase or similar)

Connect a read-only database user to the `analytics` schema. Suggested first board: weekly discovery funnel, weekly matching funnel, daily cost against the two global caps, feedback summary, `v_ops_signals` as number tiles. Pilot success metrics (M7): share of opportunities rated useful, share of proposals accepted, share of mutual matches with contact.

## Retention and opt-out

- Raw rows of `analytics_event`, `llm_call` and `external_call` live in monthly partitions. The daily partition job drops partitions older than `ANALYTICS_RETENTION_MONTHS` (13) and creates the next ones (each with RLS).
- A user can switch analytics off (`analytics_opt_out` in `PATCH /api/v1/me/notifications`). `POST /events` then stores nothing for that account; the decision is cached for up to a minute.
- Account erasure removes the user's events and sessions and anonymises cost rows (see the deletion page).
