# discovery — bounded context

**Purpose:** analysis runs, literature corpus, clusters, opportunities and evidence (ARCHITECTURE §3.2).
**Key aggregates:** `AnalysisRun`, `Opportunity`.

**Layout** (same for every context):

| Folder | Holds |
|---|---|
| `domain/` | Entities, value objects and invariants. Imports nothing from the project. |
| `application/` | Use cases. Imports `domain` and `ports` only. |
| `ports/` | Repository and outbound interfaces for this context. |
| `adapters/` | Implementations of the ports (persistence, external services). |

The import rules are enforced by import-linter (`make contracts`).

**Status:** no code yet, milestone M3 for the gap engine and M4 for the opportunities UX (`docs/IMPLEMENTATION_PLAN.md` §2).
