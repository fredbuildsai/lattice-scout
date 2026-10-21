# feedback

Ratings of opportunities (and, from M5, matches). One rating per author and target; rating again replaces the earlier one.

| Layer | Contents |
|---|---|
| `domain/feedback.py` | `Feedback`, `Rating` (`up`/`down`), `Reason` (five chips), `feedback_id` (uuid5 of author and target) |
| `ports/repos.py` | `FeedbackRepo`, `TargetCheck` (the target must be the author's own visible opportunity), `FeedbackUnitOfWork` |
| `application/give_feedback.py` | `GiveFeedback.execute` and `.mine` |
| `adapters/sql.py` | upsert on `(author_id, target_type, target_id)`; the target check reads the `opportunity` table |

API: `POST /api/v1/feedback`, `GET /api/v1/feedback?target_type&target_id`. Admin view: `analytics.v_feedback_summary` (migration 0007).
An unknown target and someone else's target both answer 404. Decisions: DECISIONS 76.
