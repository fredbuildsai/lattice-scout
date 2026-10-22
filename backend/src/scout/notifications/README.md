# notifications — bounded context

**Purpose:** outbound email, templating and preferences (ARCHITECTURE §3.2).
**Key aggregate:** `Notification`.

**Layout** (same for every context):

| Folder | Holds |
|---|---|
| `domain/` | Entities, value objects and invariants. Imports nothing from the project. |
| `application/` | Use cases. Imports `domain` and `ports` only. |
| `ports/` | Repository and outbound interfaces for this context. |
| `adapters/` | Implementations of the ports (persistence, external services). |

The import rules are enforced by import-linter (`make contracts`).

**Status:** M6 slice D. `domain/notification.py` (templates, `Prefs`, `render`), `application/service.py` (`Notifications`, `MatchMailNotifier`, `WeeklyDigest`), `adapters/senders.py` (console, Resend), `adapters/sql.py` (preferences and delivery log). Mails are jobs (`notification.send`); copy never names a person or a decision (DECISIONS 95-98).
