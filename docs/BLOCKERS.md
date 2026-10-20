# Blockers

Things that cannot be done in the cloud environment. Each entry: what, why, the fallback in use, and **exact steps for the human**.

| # | Opened | What | Why | Fallback in use | Human action | Status |
|---|---|---|---|---|---|---|
| B2 | 2026-10-20 | Live OpenAI / OpenAlex / Supabase | Not reachable from the VM | Fakes + fixtures; live tests `needs_network` skipped | Add `OPENAI_API_KEY` etc. to the environment and run `pytest -m needs_network` | open (expected) |
| B3 | 2026-10-20 | llm-router keeps response text in its ledger | upstream has no `store_text=False` | In-memory ledger (DECISIONS 23) | Add the option to llmrouter-free, then drop the in-memory workaround | open |

Known in advance (open them when you hit them):
- **llm-router source**: if neither a checked-out `llm-router` repository nor `LLM_ROUTER_SOURCE` is available → D4 fallback (OpenAIAgentsDirectAdapter). Human: add the repo to the session or set `LLM_ROUTER_SOURCE`.
- **Live services**: OpenAI key, OpenAlex API key, Supabase project → live tests stay skipped. Human: add secrets to the environment and run the unblock prompt.
