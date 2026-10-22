# Prompt findings

Failing cases from the live model eval (`eval/run_eval.py --live`, OPERATIONS "Live model eval"), one file per agent: `docs/prompt-findings/<Agent>.md` (for example `Critic.md`). Create the file the first time that agent fails a rule.

## Tuning loop

1. Run the eval on one deployment, `--runs 2` or more. Read `eval/reports/live-<date>.md`: the "Failing rules" column and the `failure_codes` of the agent (for example `planted_claim_approved`, `anonymity_leak`, `quote_not_verbatim`, `invented_evidence_id`).
2. Open the matching entry in `eval/reports/live-<date>.json` (`records`): case id, run, status, failure codes. Cases are in `eval/live_eval/cases.py`; the input is rebuilt from the case id, so no model text is stored.
3. Add a row to `docs/prompt-findings/<Agent>.md` (template below). Decide: prompt wording (hand to the `prompt-engineer`, bump the prompt version and changelog in `prompts/<Agent>.md`), a code guard that already covers it (then note "guarded by ..."), a threshold that is too strict, or a model that is not fit (record the deployment).
4. Rerun only that agent: `--agents Critic`. Record the before and after numbers in the same file.
5. A change to a prompt needs the FakeLLM fixtures checked (`scripts/make_*_fixtures.py --check`) and `make check`.

## Template

```markdown
# <Agent> findings

| Date | Prompt version | Deployment | Case id | Failure code | What the model did | Fix (prompt / guard / threshold / model) | After |
|---|---|---|---|---|---|---|---|
```

Keep model output out of this folder when it quotes a real person or a real document; describe it. Synthetic and public-title cases may be quoted briefly.
