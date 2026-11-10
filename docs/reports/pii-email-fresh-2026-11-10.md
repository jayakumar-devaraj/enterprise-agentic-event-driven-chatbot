# Email leftmost-match fix on fresh cases, 2026-11-10

Targets and cases pre-registered in `pii-email-targets-2026-11-10.md` (commit `0a6d4ba`, pushed
before the pattern changed). Command: `uv run python tests/eval/pii_recall.py email-fresh` →
`pii_email_fresh.json`.

## Fix
The obfuscated-email pattern in `chat_common.scrubber` no longer accepts a match that is followed by
another `at <domain>`. In "me at maria dot lopez at example dot com" the regex then skips
`me at maria dot lopez` and matches the address itself.

## Verdicts
| Target | Result | Verdict |
|---|---|---|
| `email_after_at` redaction recall, 8 of 8 | 8/8 | Pass |
| False positives in the 4 negatives, 0 | 1 | **Fail** |

The false positive is "Look at the dot at the top of the page": `Look at the dot at` was read as an
email whose domain is `the.at`, since `at` is a real top-level domain. **It was not introduced by
this fix.** The pattern as it was before the fix (`eb82cab`) matches the same span. The words that
separate an obfuscated address (`at`, `dot`) can also be read as its labels, and no rule against
that was written. Over-redaction leaks nothing, but it changes ordinary sentences. It is recorded in
`docs/known_limitations.md` and is not fixed here, for the same reason as before: a fix written
after this score would be tuned on these cases.

Other spans: spaCy still tags `tom dot reyes` as `PERSON` and `uk` as `LOCATION` inside addresses
that the email span already covers.

## Regression checks (sets that saw the defect, so not verdicts)
Each `after` file is `measure_broad` from `tests/eval/pii_recall.py` on that fixture, run on the
fixed scrubber, written as in the script in `pii-heldout-2026-11-10.md`.

| Set | Before the fix | After | Files |
|---|---|---|---|
| Held-out, obfuscated (n = 14) | 11/14 | 12/14 | `pii_heldout.json` → `pii_heldout_after_email_fix.json` |
| Held-out, address / controls / negatives' false positives | 19/19, 6/6, 0 | 19/19, 6/6, 0 | same |
| Broad, structured identifiers pooled (n = 14) | 12/14 | 13/14 | `pii_broad_2026-11-10.json` → `pii_broad_after_email_fix.json` |
| Broad, false-positive list | 11 spans | the same 11 | same |

The held-out obfuscated score of 12/14 would pass that set's 0.80 target, but **the pre-registered
held-out verdict stays Fail (11/14)**. The held-out set saw this defect, so its later score is not
evidence. The remaining held-out misses are the space-only IP and the spelled-out card.
