---
name: JointIdeas
version: 1.0.0
tier: reasoning
output_type: JointIdeaList
inputs: |
  - a: ResearcherSummary + {display_name}
  - b: ResearcherSummary + {display_name}
  - a_signals: list of {insight_ref, text}   # only vault insights A consented to share for matching; may be empty
  - b_signals: list of {insight_ref, text}   # same for B
  - shared_topics, a_only_methods, b_only_methods
changelog:
  - 1.0.0 (2026-10-20): initial version
---

Two researchers both said yes to working together. Propose 1–3 joint project ideas they could discuss in a first call. Both will read the same ideas.

## Rules
1. Each idea needs both people: it must use at least one thing from A and one from B (method, dataset, result, topic expertise). Say which in `uses_from_a` and `uses_from_b`, with `source_refs` (`work:W…` or `insight:…`).
2. Use only the inputs. Vault signals are private: refer to them in general terms ("your unpublished results on X") and never quote them verbatim, because the other side will read the idea too.
3. Be concrete: a research question, a first experiment or analysis, and a possible output (paper, dataset, proposal).
4. `title` max 12 words; `question` max 30 words; `first_step` max 25 words.
5. Do not promise funding, outcomes or novelty. Novelty has not been measured for these ideas; say "worth checking the literature" if relevant.

## Output (JSON matching `JointIdeaList`)
```json
{
  "ideas": [
    {
      "title": "Screening lignin monomers by life-cycle impact",
      "question": "Which enzymatically produced lignin monomers give the lowest cradle-to-gate impact as polymer precursors?",
      "uses_from_a": [{"text": "LCA models for bio-based packaging", "source_refs": ["work:W1"]}],
      "uses_from_b": [{"text": "Enzymatic depolymerisation yields", "source_refs": ["work:W9"]}],
      "first_step": "Share yield data for three monomers and run a screening LCA on each.",
      "possible_output": "short communication + joint proposal"
    }
  ]
}
```
