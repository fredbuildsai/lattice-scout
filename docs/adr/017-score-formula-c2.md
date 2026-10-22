# ADR 017: Matching score formula c2 (overlap coefficient) and a calibrated threshold

- **Status:** accepted, provisional until pilot feedback (M7)
- **Date:** 2026-10-21

## Context

Formula `c1` used the weighted Jaccard of the topic sets as `T`. Profiles keep at most 25 topics with a long tail of low weights, so the Jaccard is small for real researchers: the demo pair Foster/Moreira has `T = 0.068` and scores 0.055, below the default `MATCH_MIN_SCORE` 0.08, which was never calibrated (BACKLOG F-39).

## Decision

1. `T` becomes the overlap coefficient `sum(min) / min(sum x, sum y)` (formula `c2`). It asks how much of the lighter profile the other covers, so the tail of a broad profile no longer dilutes the score. `M`, activity, alpha (1.0) and beta (1.5) are unchanged.
2. Default `MATCH_MIN_SCORE` is 0.11.
3. `ScoreBasis.formula` records `c2` on new proposals. Stored `c1` proposals keep their numbers; `score_pair(formula="c1")` still reproduces them.

## Evidence (`scripts/calibrate_matching.py`, deterministic, no network)

Pool of 36 synthetic `example.edu` profiles from the controlled vocabularies, labels by construction. Share passing 0.11 (c2): good complementary 98%, too similar 0%, unrelated 4%. Under c1 the same pool needs about 0.056 to keep the good pairs and then 9% of unrelated pairs pass. The demo pair scores 0.119 under c2 (cosine 0.113 was the alternative: no better separation, harder to explain). Foster/Moreira score above 98% of the bad pairs: it passes, with a thin margin (0.119 vs 0.11) because the pair is only weakly related (six shared low-weight topics).

## Limits

The synthetic pool fixes the relative order of the labels, not the absolute level of real pairs; the demo pair is the only real anchor and Chesneau's pairs are not evaluated yet (blocker B7). Tune on pilot feedback in M7.
