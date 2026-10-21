# ADR 015: Vault data goes only to sanctioned LLM endpoints

- **Status:** accepted
- **Date:** 2026-10-23 (M6 review blocking #1; supersedes the privacy assumptions of DECISIONS 105)

## Context

ARCHITECTURE section 1, principle 2: vault content never leaves the backend except to an LLM provider under a
no-training agreement. DECISIONS 105 moved all calls to the free tiers of OpenRouter, Groq and Mistral through
llm-router. Nothing in the repository records those providers' data-use terms. Free tiers are commonly offered on
terms that allow logging, retention or training on inputs unless an opt-out is set. The privacy tab and user page
said "Scout never trains models on your files", which the code could not back up.

## Decision

1. **Data classes.** Every agent declares `data_class: public | vault` in its prompt front-matter (`prompts/*.md`).
   `AgentSpec.data_class` defaults to `vault` (fail closed). Vault means the input contains document text,
   passages, insights or profile text derived from them. Today `ClusterLabeler` and `MatchExplainer` are public;
   `InsightExtractor`, `InsightTopicTagger`, `QueryPlanner`, `GapAnalyst`, `Critic`, `MethodExtractor` and
   `JointIdeas` are vault.
2. **Sanctioned endpoints.** A deployment in `config/llm_router.yaml` can carry `sanctioned: true`. A vault call may
   use it only if its provider (the model prefix) is also in `LLM_VAULT_SANCTIONED_PROVIDERS`. Both are needed:
   the file says "we reviewed this deployment", the setting says "this installation accepts the provider". Both
   default to empty, so out of the box no remote endpoint receives vault data. No deployment is marked today.
3. **Enforcement.** `LlmRouterAdapter` maps a vault call to a derived route `<route>__vault` that contains only
   sanctioned deployments. If there is none, it raises `VaultDataRefused` (HTTP 503 `vault_data_refused`) before
   any request is made and logs agent name and route only, never content. `MeteringCompletion` re-checks the
   model before every provider request of a vault call. `OpenAIAgentsDirectAdapter` refuses unless `openai` is on
   the allowlist. Public calls may use any deployment. The fake backend never leaves the process.
4. **Production start-up.** `APP_ENV=prod` with `llm_router` or `openai_direct` is refused when
   `LLM_VAULT_SANCTIONED_PROVIDERS` is empty, unless `LLM_VAULT_ALLOW_ANY_PROVIDER=true` (an explicit waiver, off by
   default). Separately, `llm_router` (free tiers) is refused in prod unless `LLM_FREE_PROVIDERS_ACKNOWLEDGED=true`.
5. **Parser isolation.** The PDF/DOCX child process starts with a minimal environment (`PATH`, `LANG`, ...), so
   provider keys never reach code that parses untrusted files.
6. **Truthful copy.** The privacy tab and user page say that excerpts go to the configured providers, that private
   text goes only to approved ones, and that the providers have their own terms.

## Consequences

- With the shipped config, an `llm_router` deployment cannot process documents, runs or matches until the owner
  has confirmed a provider's terms (opt-outs such as OpenRouter's "do not train / do not publish prompts" setting,
  or Mistral's workspace training opt-out) and marked a deployment. This is a human step: BLOCKERS B6.
- Which providers are used is unchanged; only who may receive vault data is restricted.
- Adding an agent without a `data_class` makes it vault, so new agents cannot leak by omission.
- Import-linter: the concept lives in `ports/llm.py`; the policy and its enforcement in `adapters/llm`. Domain and
  application do not import it.
