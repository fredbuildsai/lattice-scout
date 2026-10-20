# Blockers

Things that cannot be done in the cloud environment. Each entry: what, why, the fallback in use, and **exact steps for the human**.

| # | Opened | What | Why | Fallback in use | Human action | Status |
|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — |

Known in advance (open them when you hit them):
- **llm-router source**: if neither a checked-out `llm-router` repository nor `LLM_ROUTER_SOURCE` is available → D4 fallback (OpenAIAgentsDirectAdapter). Human: add the repo to the session or set `LLM_ROUTER_SOURCE`.
- **Live services**: OpenAI key, OpenAlex API key, Supabase project → live tests stay skipped. Human: add secrets to the environment and run the unblock prompt.
