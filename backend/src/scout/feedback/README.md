# feedback

Ratings of opportunities and matches. One rating per author and target; rating again replaces the earlier one.

| Layer | Contents |
|---|---|
| `domain/feedback.py` | `Feedback`, `Rating` (`up`/`down`, opportunities), stars 1-5 (matches), `Reason` with the per-target sets `OPPORTUNITY_REASONS` and `MATCH_REASONS`, the limit of 5, `feedback_id` (uuid5 of author and target) |
| `ports/repos.py` | `FeedbackRepo`, `TargetCheck` (answered by the context that owns the target), `FeedbackUnitOfWork` |
| `application/give_feedback.py` | `GiveFeedback.execute` and `.mine`; `UNSET` / `reasons=None` keep the stored comment and reasons |
| `application/targets.py` | `TargetCheckAdapter`: asks `DiscoveryService.is_visible` and `MatchingService.is_mutual`; feedback reads no foreign table |
| `adapters/sql.py` | upsert on `(author_id, target_type, target_id)` |

API: `POST /api/v1/feedback`, `GET /api/v1/feedback?target_type&target_id`, `POST /api/v1/matches/{id}/rating`. Admin view: `analytics.v_feedback_summary`.

Rules:

- A reason must belong to the target type (domain check, and a CHECK constraint since migration 0016).
- `reasons` and `comment` left out of a request keep the stored values; `[]` and `null` clear them.
- An unknown, foreign, hidden or not-yet-mutual target answers 404 (a hidden opportunity, see the evidence rule, cannot be rated).
- Validation errors (422) come before the target check (404).

Decisions: DECISIONS 76 and 116.
