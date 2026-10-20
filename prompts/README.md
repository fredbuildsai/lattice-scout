# Runtime agent prompts

System prompts for the LLM agents that run **inside Lattice Scout** (not the Claude Code subagents in `.claude/agents/`). Contracts: ARCHITECTURE §6.4. Owner: the `prompt-engineer` subagent.

| File | Agent | Tier | Output type | Used in |
|---|---|---|---|---|
| `InsightExtractor.md` | InsightExtractor | fast | `DocumentInsights` | M2 ingestion, run step 2 |
| `QueryPlanner.md` | QueryPlanner | fast | `QueryPlan` | run step 3 |
| `ClusterLabeler.md` | ClusterLabeler | fast | `ClusterLabel` | run step 5 |
| `InsightTopicTagger.md` | InsightTopicTagger | fast | `InsightTopicTags` | run step 6 |
| `GapAnalyst.md` | GapAnalyst | reasoning | `GapAnalysis` | run step 8 |
| `Critic.md` | Critic | reasoning | `CriticVerdict` | run step 9 |
| `MethodExtractor.md` | MethodExtractor | fast | `MethodSet` | M5 researcher profiles |
| `MatchExplainer.md` | MatchExplainer | fast | `MatchRationale` | M5 proposals |
| `JointIdeas.md` | JointIdeas | reasoning | `JointIdeaList` | M5 after mutual |

## Conventions
- **Front-matter** (YAML): `name`, `version` (semver), `tier` (`fast` | `reasoning`, resolved by llm-router), `output_type` (the Pydantic class), `inputs`, `changelog`.
- The agent factory (plan M2 B5) loads the body below the front-matter as the system prompt and stores `name@version` on every `llm_call`, run, opportunity and claim.
- `{{placeholders}}` are filled by the factory from config (e.g. `{{field_name}}`, `{{today}}`). The per-call input is sent as the user message, as JSON.
- The JSON shapes in the prompts mirror the Pydantic models. **If you change a model, change the prompt and bump its version in the same commit.**
- Grounding rules shared by every agent: use only IDs present in the input; quotes are verbatim; prefer "insufficient evidence" to guessing; never output personal data about third parties.
