# feedback — bounded context

**Purpose:** ratings on opportunities and matches, used for evaluation (ARCHITECTURE §3.2).
**Key aggregate:** `Feedback`.

**Layout** (same for every context):

| Folder | Holds |
|---|---|
| `domain/` | Entities, value objects and invariants. Imports nothing from the project. |
| `application/` | Use cases. Imports `domain` and `ports` only. |
| `ports/` | Repository and outbound interfaces for this context. |
| `adapters/` | Implementations of the ports (persistence, external services). |

The import rules are enforced by import-linter (`make contracts`).

**Status:** no code yet, milestone M4 (`docs/IMPLEMENTATION_PLAN.md` §2).
