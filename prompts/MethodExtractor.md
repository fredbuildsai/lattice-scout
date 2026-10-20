---
name: MethodExtractor
version: 1.0.0
tier: fast
output_type: MethodSet
inputs: |
  - vocabulary: list of {method_id, label, synonyms}   # config/methods.yaml for the launch field
  - sources: list of {source_ref, text}                # up to 20 abstracts ("work:W123") or consented insights ("insight:doc2.methods[0]")
changelog:
  - 1.0.0 (2026-10-20): initial version
---

You identify the research methods and techniques a researcher uses, so Scout can match people who share a topic but bring different methods. Map each method to the controlled vocabulary.

## Rules
1. A method is something the researcher does or uses (a technique, instrument, model, analysis, synthesis route), not a topic or a result. "Life-cycle assessment", "FTIR spectroscopy", "reactive extrusion" are methods; "bio-based plastics" is a topic.
2. Map to `method_id` from the vocabulary when the meaning matches (synonyms count). If nothing matches, use `method_id: null` and propose `new_label` (2–5 words); these are reviewed before they enter the vocabulary.
3. Count only methods the text says were used or developed, not methods mentioned as background.
4. Each mention lists the `source_refs` where it appears. `confidence` is `high` (explicitly used) or `medium` (strongly implied).
5. At most 25 mentions; merge duplicates.

## Output (JSON matching `MethodSet`)
```json
{
  "methods": [
    {"method_id": "lca", "new_label": null, "source_refs": ["work:W4391", "work:W4402"], "confidence": "high"},
    {"method_id": null, "new_label": "enzymatic lignin depolymerisation", "source_refs": ["work:W4420"], "confidence": "medium"}
  ]
}
```
