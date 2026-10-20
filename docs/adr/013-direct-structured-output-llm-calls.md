# ADR 013: Direct structured-output calls through llm-router (ADR-010 follow-up)

- **Status:** accepted
- **Date:** 2026-10-20

## Context
ADR-010 chose our llm-router (`llmrouter-free`) for LLM access and left open how it meets the OpenAI Agents SDK.
The router is synchronous, chat-completions only (via LiteLLM) and returns text plus a parsed value; it does not
pass tool calls through and does not report cached or reasoning tokens.

## Options considered
1. Custom `agents.Model` provider that forwards to the router: we would have to rebuild the SDK's tool loop.
2. Direct calls: send `[system=instructions, user=input JSON]`, constrain the output with a JSON schema, validate into
   the agent's Pydantic type with the router's `json_validator`.
3. Use the Agents SDK with its default OpenAI provider only (this is the D4 fallback).

## Decision
Option 2 for `LlmRouterAdapter`. Tools such as `lookup_work` / `search_similar` (ARCHITECTURE §6.4) become
context fetched in advance from the stored run corpus; evidence is verified in code anyway. Option 3 stays as
`OpenAIAgentsDirectAdapter` behind the same `LLMPort`. A thin `MeteringCompletion` wraps `litellm.completion` to
read cached/reasoning tokens for every provider attempt, so each attempt becomes one `llm_call` row.

## Consequences
- One place meters tokens and cost (price table in `config/llm_prices.yaml`), per attempt, with failures included.
- Agents cannot call tools mid-run; revisit if a use case needs it (swap to option 1 behind the port).
- The router's SQLite ledger keeps response text until process exit (BACKLOG: upstream `store_text=False`).
