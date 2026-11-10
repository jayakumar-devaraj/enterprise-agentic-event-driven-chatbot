# Held-out PII evaluation, 2026-11-10

Targets and set pre-registered in `pii-targets-2026-11-10.md` (commit `49083d3`, pushed before the
recognizers were written). Audit follow-up 1.3. Scrubber: Presidio + spaCy `en_core_web_lg`, plus
four pattern recognizers added in `chat_common.scrubber` (street address and PO Box, obfuscated
email, obfuscated IPv4, card numbers with other separators checked by Luhn).

Commands: the fixed scrubber is `uv run python tests/eval/pii_recall.py heldout` →
`pii_heldout.json`. The baseline (`pii_heldout_baseline.json`) and the broad re-run
(`pii_broad_2026-11-10.json`) come from the same harness function, `measure_broad`, run from the
repository root with `uv run python` on the script below. The baseline drops the four new
recognizers, which leaves the scrubber as on `main` (`8e82c34`), since this change to
`scrubber.py` is the only difference. The original `pii_broad.json` of 2026-10-25 is left
unchanged.

```python
import json, sys
from pathlib import Path
sys.path.insert(0, "tests/eval")
import pii_recall as h
from chat_common import scrubber as s

def write(report, path):
    Path(path).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

write(h.measure_broad(h.HELDOUT_FIXTURE), "docs/reports/pii_heldout.json")
write(h.measure_broad(h.BROAD_FIXTURE), "docs/reports/pii_broad_2026-11-10.json")
s.get_scrubber.cache_clear()
s._extra_recognizers = lambda: []  # the scrubber as on main 8e82c34
write(h.measure_broad(h.HELDOUT_FIXTURE), "docs/reports/pii_heldout_baseline.json")
```

## Verdicts (held-out set, 50 sentences)
| Target | Baseline (main) | Fixed (95% Wilson) | Verdict |
|---|---|---|---|
| Address category redaction recall ≥ 0.80 (n = 19) | 3/19 = 0.158 | 19/19 = 1.0 (0.832–1.0) | Pass |
| Obfuscated category redaction recall ≥ 0.80 (n = 14) | 1/14 = 0.071 | 11/14 = 0.786 (0.524–0.924) | **Fail** |
| Controls still redacted, 6 of 6 | 6/6 | 6/6 | Pass |
| False positives in the 14 negatives ≤ 2 | 0 | 0 | Pass |
| Precision ≥ 0.80 for `LOCATION`, `EMAIL_ADDRESS`, `IP_ADDRESS`, `CREDIT_CARD` | — | 1.0 each (21, 10, 3, 3 spans) | Pass |

## The three obfuscated misses
- `maria dot lopez at example dot com` in "Reach me at maria dot lopez at example dot com". This is
  a **defect in the email pattern**: the regex starts at the leftmost possible local part, so it
  matches `me at maria dot lopez` and never covers the real address. The broad set's
  `jane dot doe at example dot com` is missed for the same reason. It is not fixed here: a fix
  written after this score would be tuned on the held-out set, so it needs its own fresh cases.
- `192 168 0 14`: four numbers separated only by spaces. No recognizer for this was built, because
  plain number runs are too common in support text. It was a judgement case in the set.
- The card spelled out in words: expected to stay a miss (stated in the targets).

## Other observations
- spaCy's `PERSON` false positives are unchanged (`Larkspur Lane`, `Willow Way`, `maria dot lopez`,
  a sentence-initial `Email`). In the address sentences the street recognizer now covers the same
  text, so nothing leaks, but `PERSON` precision on this set stays 0.2 (5 spans).
- No look-alike negative was redacted, before or after.

## Broad set re-run (no longer held out, so not a verdict)
| Measure (2026-10-25 target) | 2026-10-25 | 2026-11-10 |
|---|---|---|
| Structured identifiers pooled (≥ 0.90) | 10/14 = 0.714 | 12/14 = 0.857 |
| LOCATION redaction recall (≥ 0.80) | 16/30 = 0.533 | 22/30 = 0.733 |
| False positives in the 20 negatives (≤ 3) | 2 | 2 |

The false-positive list is identical to 2026-10-25. Remaining broad misses: the `jane dot doe`
email (the defect above), the spelled-out card, `1600 Pennsylvania Avenue NW` (the `NW` suffix after
the street type), `55 Rue de Rivoli` (a French street form), `Little Whinging` and `Surrey` (place
names without a number), three places in non-English sentences, `Nguyễn Văn An`, `Hope` and
`Reading`. Non-English place names stay out of scope for this change (decided 2026-11-10).
