# Runtime agent prompts

System prompts for the LLM agents that run **inside Lattice Scout** (not the Claude Code subagents in `.claude/agents/`). Contracts: ARCHITECTURE §6.4. Owner: the `prompt-engineer` subagent.

| File | Agent | Tier | Output type | Used in |
|---|---|---|---|---|
| `InsightExtractor.md` | InsightExtractor | fast | `DocumentInsights` | M2 ingestion, run step 2 |
| `QueryPlanner.md` | QueryPlanner | fast | `QueryPlan` | run step `plan_queries` |
| `ClusterLabeler.md` | ClusterLabeler | fast | `ClusterLabel` | run step `group_by_topic` |
| `InsightTopicTagger.md` | InsightTopicTagger | fast | `InsightTopicTags` | run step `coverage` |
| `GapAnalyst.md` | GapAnalyst | reasoning | `GapAnalysis` | run step `analyse` |
| `Critic.md` | Critic | reasoning | `CriticVerdict` | run step `verify` |
| `MethodExtractor.md` | MethodExtractor | fast | `MethodSet` | M5 researcher profiles |
| `MatchExplainer.md` | MatchExplainer | fast | `MatchRationale` | M5 proposals |
| `JointIdeas.md` | JointIdeas | reasoning | `JointIdeaList` | M5 after mutual |

## Conventions
- **Front-matter** (YAML): `name`, `version` (semver), `tier` (`fast` | `reasoning`, resolved by llm-router), `output_type` (the Pydantic class), `inputs`, `changelog`.
- The agent factory (plan M2 B5) loads the body below the front-matter as the system prompt and stores `name@version` on every `llm_call`, run, opportunity and claim.
- `{{placeholders}}` are filled by the factory from config (e.g. `{{field_name}}`, `{{today}}`). The per-call input is sent as the user message, as JSON.
- The JSON shapes in the prompts mirror the Pydantic models. **If you change a model, change the prompt and bump its version in the same commit.**
- Grounding rules shared by every agent: use only IDs present in the input; quotes are verbatim; prefer "insufficient evidence" to guessing; never output personal data about third parties.
- Every prompt says that input text (documents, abstracts, titles, summaries) is data, never instructions.
- Discovery agent I/O models live in `backend/src/scout/discovery/domain/agent_io.py`; vault ones in `scout/vault/domain/insights.py`; matching ones in `scout/matching/domain/agent_io.py` (no names, institutions, years, countries or career data ever go in).
- FakeLLM fixtures (`backend/tests/fixtures/llm/<Agent>/<input_hash>.json`) are generated, not hand-edited: `scripts/make_insight_fixtures.py` (InsightExtractor) and `scripts/make_discovery_fixtures.py` (the five discovery agents, for `eval/cases/*.yaml`; it also writes `<input_hash>.input.json`) and `scripts/make_matching_fixtures.py` (the three matching agents for the Foster/Moreira demo pair; synthetic pairs have no fixture and use the template). Prompt text is not part of the hash, so a prompt edit alone does not invalidate fixtures; an input-model or pipeline change does (run the script with `--check`).
