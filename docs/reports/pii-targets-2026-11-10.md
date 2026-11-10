# PII scrubbing targets for the held-out set, pre-registered before any fix

Committed together with `tests/fixtures/pii_heldout.jsonl` and its scoring mode, **before the
street-address and obfuscated-identifier recognizers are written and before the set is scored** by
any scrubber, old or new. Audit follow-up 1.3 (`docs/reports/audit-followups-2026-11-06.md`).

## Why a new set
`pii-broad-2026-10-25.md` failed structured identifiers (10/14) and LOCATION (16/30). Its
disposition says a fix tuned on that set would make its next score meaningless, so fixes need a
fresh held-out set written first. The verdicts below come from this set. The broad set is re-run
too, but it is no longer held out: its new numbers are reported, not used as a verdict.

## Set
50 sentences, 39 labelled entities, every PII value in a sentence labelled. All values are
fictional: `example.*` domains, documentation IP ranges, published test card numbers.
- 16 address sentences: 16 street parts (number, street, suffix, and unit when present; one PO Box)
  and 3 cities. 19 values, all `LOCATION`.
- 14 obfuscated identifiers: 8 emails (`dot`/`at` words, brackets, braces, parentheses, upper
  case, spaced `@`), 3 IPs (spelled dots, `[.]`, spaces), 3 cards (spelled out, underscores, Amex
  4-6-5 spacing).
- 6 controls in plain formats (email, phone, IP, card, person, city) that are caught today. They
  guard against regressions.
- 14 negatives: look-alikes with no PII (versions, a spelled version, a tracking number, an error
  code, `at` and `dot com` in ordinary speech, numbers followed by capitalised words).

**Disclosure:** the agent that wrote this set also writes the recognizers. It wrote the set after
reading the broad set's failure categories, so the set is not blind to them: it targets the same
categories with different sentences and formats. Some calls are judgement: `192 168 0 14` is
labelled an IP, and `Order 7741 Elm Edition` is labelled a negative. The spelled-out card is
expected to stay a miss; no recognizer for it is planned.

## Metrics
As in the broad set (`tests/eval/pii_recall.py`): **redaction recall** means a labelled value is
fully covered by a detected span of any type. **Precision** means detected spans that overlap a
labelled entity of the same type, over all detected spans of that type. 95% Wilson intervals are
given throughout. Run: `uv run python tests/eval/pii_recall.py heldout` →
`docs/reports/pii_heldout.json`.

## Targets (point estimates)
| Measure | Target |
|---|---|
| Redaction recall, address category (n = 19) | ≥ 0.80 |
| Redaction recall, obfuscated category (n = 14) | ≥ 0.80 |
| Controls still redacted (n = 6) | 6 of 6 |
| False positives in the 14 negative sentences | ≤ 2 |
| Precision of each type a new recognizer emits (`LOCATION`, `EMAIL_ADDRESS`, `IP_ADDRESS`, `CREDIT_CARD`) | ≥ 0.80 |

The scrubber on `main` (`8e82c34`) is scored on this set in the same run as the fixed one, so the
change is measured, not assumed.
