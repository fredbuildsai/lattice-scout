# Decisions log

Small decisions taken during implementation that are not in ARCHITECTURE §0.1. Significant ones also get an ADR in `docs/adr/`.

| # | Date | Context | Decision | Consequence | By |
|---|---|---|---|---|---|
| 1 | 2026-10-20 | Hand-off bundle | Main session on Sonnet; specialists in `.claude/agents/` (D19) | Lower cost; Opus only where reasoning matters | product owner |
| 2 | 2026-10-20 | Branch name | The session harness designates `claude/zealous-curie-7m18vd` for all work; `m0-foundations` is not created. Later milestones stack on this branch unless the harness says otherwise | PR for M0 is opened from this branch | lead dev |
| 3 | 2026-10-20 | llm-router source (D4) | A second repository `llmrouter-free` (fredbuildsai) is checked out at `/home/user/llmrouter-free`; it is the "llm-router" of D4. `LlmRouterAdapter` wraps it; it is installed on demand (`uv pip install -e ../llmrouter-free` or `LLM_ROUTER_SOURCE`), not in the default lock file, so CI stays hermetic. Blocker B1 not opened | `LLM_BACKEND=llm_router` needs the package + provider keys | lead dev |
| 4 | 2026-10-20 | Python version | Backend pins Python `>=3.12,<3.13` (llmrouter-free requires `<3.13`) | uv uses 3.12.13 | lead dev |
