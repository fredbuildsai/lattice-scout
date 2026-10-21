# Notifications

Context `notifications` (DECISIONS 95-98).

| Mail | Trigger | Preference |
|---|---|---|
| Run finished | `RunAnalysis` ends `done` | `email_runs` (on) |
| New suggestion | `ComputeMatches` creates a proposal | `email_matches` (on) |
| Mutual match | both sides accepted | `email_matches` (on) |
| Weekly summary | job `notification.digest`, Mondays 07:00 UTC | `email_digest` (off) |

The sign-in mail belongs to Supabase Auth (`docs/EMAIL_TEMPLATES.md`).

## How a mail is sent

`Notifications.notify` queues `notification.send` with a dedupe key per account, template and reference. The handler checks the preferences, the account (not deleted) and the delivery log, renders the text and calls the `EmailSender`. A provider error raises and the worker retries; the log row is written only after a successful send.

Mail text is plain: a sentence, a link into the app and a footer. It never carries names, papers, counts, contact details or a decline. The `MatchNotifier` port has no decline method, so a decline cannot be mailed.

## Configuration

`EMAIL_PROVIDER` (`console` logs the mail, `resend` sends it), `EMAIL_API_KEY`, `EMAIL_FROM`, `APP_BASE_URL`. `console` is refused in production.

API: `GET` and `PATCH /api/v1/me/notifications`.
