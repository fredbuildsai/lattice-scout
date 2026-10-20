# Prompts for Claude Code cloud sessions

Copy the prompt into a new session at claude.ai/code with the `lattice-scout` repository selected (and `llm-router` added as a second repository if you can).

## Before every session (you, the human)

| Setting | Choose |
|---|---|
| Model | **Sonnet** (every milestone). The subagents switch to Opus or Haiku on their own (plan §0.4c). |
| Permission mode | **Auto** if available, otherwise **Accept edits** |
| Environment | the one set up per plan §0.9 (Trusted network, env vars, setup script) |
| Repositories | `lattice-scout` (+ `llm-router`) |
| Branch | `main` (Claude switches to the milestone branch itself) |

One session ≈ one milestone or part of one. When a session ends, start the next with the **resume prompt**. Check the cloud credit bar after M0 to calibrate the cost per milestone.

---

## Kickoff prompt (first session only)

```
You are the lead developer of Lattice Scout and you work unsupervised in this cloud session.

1. Read CLAUDE.md, then PROGRESS.md, then docs/IMPLEMENTATION_PLAN.md §0 (execution protocol, especially §0.4b documentation and §0.4c division of work) and §2 M0, then docs/ARCHITECTURE.md §0.1 (decision register). Read other ARCHITECTURE sections only when a task needs them.
2. Start milestone M0 — Foundations on branch m0-foundations. Open today's session journal in docs/journal/ first.
3. Work task by task following the working loop: tests first, implement, make check, commit, push. Call the specialist subagents in .claude/agents/ exactly at the moments listed in plan §0.4c (architect before [architect] tasks, test-engineer before [test-first] tasks, frontend-dev for [frontend] tasks, debugger after two failed attempts, doc-writer then reviewer at the end of the milestone). Only you commit and push.
4. Document as you go (plan §0.4b). Record decisions in docs/DECISIONS.md and blockers in docs/BLOCKERS.md, use the fallback, and keep going. Stop only for the hard stops in plan §0.6.
5. When M0 meets its acceptance criteria and the Definition of Done, open the PR as described in plan §0.4 (merge policy in PROGRESS.md), then continue with M1 in this session if there is room.
6. Before the session ends or your context runs low: commit and push everything, update PROGRESS.md and finish the session journal with next steps.

Begin now. Do not ask me questions; decide, record and continue.
```

## Resume prompt (every following session)

```
Continue building Lattice Scout unsupervised.

1. Read CLAUDE.md and PROGRESS.md (current milestone, branch, merge policy, notes, blockers). Read docs/IMPLEMENTATION_PLAN.md §0 and the current milestone in §2. Read ARCHITECTURE sections only on demand.
2. Check out the branch from PROGRESS.md, run make check, open a new session journal in docs/journal/.
3. Continue with the first unchecked task. Delegate per plan §0.4c (architect, test-engineer, prompt-engineer, frontend-dev, debugger, doc-writer, reviewer) at the listed moments; only you commit and push.
4. Document every step (plan §0.4b). Decide, record and continue; stop only for the hard stops in plan §0.6.
5. At a milestone boundary: doc-writer, reviewer, fix blocking findings, PR per plan §0.4, then start the next milestone if there is room.
6. Before the session ends: commit and push, update PROGRESS.md, finish the journal with next steps.

Do not ask me questions.
```

## Optional: focused prompts

**Only one milestone, then stop** (useful to control spend):
```
Continue Lattice Scout as in the resume prompt, but stop after the current milestone's PR is open. Then write in PROGRESS.md what the next session should start with.
```

**After you change something by hand** (merged a PR, added a secret, edited the plan):
```
I changed: <what>. Read PROGRESS.md, reconcile it with the repository state (git log, open PRs), update PROGRESS.md and docs/DECISIONS.md accordingly, then continue as in the resume prompt.
```

**Unblock** (after you supplied a credential or the llm-router source):
```
Blocker <B-number> in docs/BLOCKERS.md is resolved: <how>. Replace the fallback with the real adapter, run its needs_network test once if the network allows, record the result, close the blocker, then continue as in the resume prompt.
```

## Automating restarts (optional)
A scheduled routine in Claude Code can fire the resume prompt every few hours so the build continues while you are away. Keep "stop after the current milestone" in the prompt if you want to review each milestone before the next one starts.
