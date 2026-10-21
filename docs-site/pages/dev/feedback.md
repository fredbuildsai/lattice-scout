# Feedback

The `feedback` context stores ratings of opportunities (thumbs and reason chips) and of mutual matches (1 to 5 stars). The package README at `backend/src/scout/feedback/README.md` has the layer table; this page has the rules a client needs.

## Endpoints

| Method and path | Purpose |
|---|---|
| `POST /api/v1/feedback` | Rate an opportunity (`rating` up or down) or a match (`stars: 1..5`). |
| `GET /api/v1/feedback?target_type=&target_id=` | Your own rating, or 404 when there is none. |
| `POST /api/v1/matches/{id}/rating` | Stars for a mutual match. |

## Semantics

- One row per author and target. A new rating replaces the old one; `created_at` is kept.
- **Partial updates.** Leave `reasons` out to keep the stored chips, send `[]` to clear them. Leave `comment` out to keep it, send `null` or `""` to clear it. The card thumbs rely on this.
- **Reasons per target.** Opportunities: `spot_on`, `already_knew`, `not_feasible`, `wrong_field`, `too_vague`. Matches: `methods_fit`, `right_seniority`, `shared_timeline`, `too_similar`, `already_knew_them`. At most 5. Anything else is a 422.
- **Who may rate.** The author's own opportunity that is still visible under the evidence rule (at least two verified items), or a mutual match the author takes part in. The check calls the discovery and matching application services; this context reads no foreign table.
- The comment is private to Scout. It is never shown to the other person of a match.

## Analytics

`analytics.v_feedback_summary` counts ratings per day, target type, rating and reason.
