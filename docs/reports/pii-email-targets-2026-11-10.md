# Fresh cases for the email leftmost-match defect, pre-registered before the fix

Committed with `tests/fixtures/pii_email_fresh.jsonl` **before the email pattern is changed and
before these cases are scored**. Follows `pii-heldout-2026-11-10.md`, which found the defect: in
`me at maria dot lopez at example dot com`, the pattern matches `me at maria dot lopez`, so the
address itself leaks. The held-out set saw the defect, so it cannot judge the fix; these cases do.

## Set
12 sentences, written by the agent that writes the fix, after seeing the defect:
- 8 `email_after_at`: an obfuscated email whose local part has a `dot` or `.`, right after the word
  "at" (word forms, brackets, upper case, a two-label suffix). None appears in the broad or
  held-out sets.
- 4 negatives: ordinary sentences with "at" used twice and, in one, the word "dot".

## Targets
| Measure | Target |
|---|---|
| `email_after_at` redaction recall (n = 8) | 8 of 8 |
| False positives in the 4 negatives | 0 |

Run: `uv run python tests/eval/pii_recall.py email-fresh` → `docs/reports/pii_email_fresh.json`.
The held-out and broad sets are re-run after the fix as regression checks only, not as verdicts.
