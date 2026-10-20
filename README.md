# Lattice Scout

AI research scout for scientists: finds new research opportunities in your unpublished work, grants and the literature, and connects you with complementary researchers through double opt-in matching.

This repository starts as a **hand-off bundle** for Claude Code, which builds the MVP unsupervised in cloud sessions.

## Start here (human)
1. Push this folder to a new GitHub repository (contents at the root, including the hidden `.claude/` folder and `.gitignore`).
2. Set up Claude Code in the cloud: `docs/IMPLEMENTATION_PLAN.md` §0.9 (environment, setup script, variables).
3. Open claude.ai/code, select this repo (and `llm-router`), model **Sonnet**, and paste the kickoff prompt from `docs/claude-code-prompts.md`.
4. Follow progress in `PROGRESS.md`, `docs/journal/` and the pull requests. Resume each new session with the resume prompt.

## What is in here
| Path | What |
|---|---|
| `CLAUDE.md` | Rules Claude Code reads every session |
| `PROGRESS.md` | Where the build is; merge policy |
| `.claude/agents/` | Specialist subagents and when they are used |
| `.claude/settings.json`, `scripts/session_start.sh` | Session start hook (Postgres, dependencies) |
| `docs/ARCHITECTURE.md` | Architecture, decision register, ADRs |
| `docs/IMPLEMENTATION_PLAN.md` | Execution protocol, milestones M0–M8, acceptance criteria |
| `docs/screens/` | Design reference (web + mobile) with screen → route index |
| `prompts/` | System prompts of the in-app AI agents |
| `tests/fixtures/openalex/` | Recorded OpenAlex data for the demo researchers |
| `docs/claude-code-prompts.md` | Kickoff and resume prompts |
