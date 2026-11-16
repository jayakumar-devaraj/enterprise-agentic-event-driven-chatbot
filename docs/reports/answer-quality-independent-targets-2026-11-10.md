# Answer quality: independent re-score, pre-registered 2026-11-10

Item 1.5 of `audit-followups-2026-11-06.md`. The 2026-10-25 run (`answer-quality-2026-10-25.md`) scored
a mean of 3.4 of 5 against a target of ≥ 3.5. The scorer was the build agent, which also built the
system. Owner decision, 2026-11-10: the project owner re-scores the same 20 replies, blind.

## What is scored
The same 20 published replies in `answer_quality_replies.json`, with no new model run and the same
rubric and targets as `answer-quality-rubric-2026-10-25.md` (C1 to C5, one point each). Re-scoring
existing replies separates the scorer question from the model question. A fresh run with the real
model would change both at once.

## Blindness
`uv run python tests/eval/answer_quality_blind.py export` writes
`answer-quality-blind-sheet-2026-11-10.csv`. It holds each question, its key points, the reply and the
deterministic checks, in a fixed shuffled order (seed 20261110). It does not hold the build agent's
scores or reasons. The scorer fills C1 to C5 with 0 or 1, adds an optional note, and does not open
`answer_quality_scores.json` or `answer-quality-2026-10-25.md` before finishing. As the rubric says, a
deterministic hit (for example `has_phone=True` for C4) is not overruled without a note saying why.

## Verdict
`uv run python tests/eval/answer_quality_blind.py score <filled.csv>` writes
`answer_quality_independent.json`. **The owner's scores are the verdict** against the original targets:

| Measure | Target |
|---|---|
| C3 met | 20 / 20 |
| C4 met | ≥ 18 / 20 |
| Mean rubric score | ≥ 3.5 / 5 |
| Empty replies | ≤ 1 / 20 |

The build agent's scores become informational. The report gives agreement per criterion (count of
equal scores and Cohen's kappa) and lists every disagreement. The first filled sheet is the verdict.
It is not re-scored after the comparison is seen.

Checked before this commit: filling the sheet with the build agent's own scores reproduces its
published totals (C1 10, C2 7, C3 20, C4 16, C5 15, mean 3.4), with full agreement.
