# PII scrubbing targets, pre-registered before scoring the broad set

Committed together with `tests/fixtures/pii_broad.jsonl` and the scoring code, **before the broad
set has been scored**, so the results cannot shape the targets. These are **proposals** for the
lead, who sets recall floors (`docs/known_limitations.md`).

## Set
60 sentences, 62 labelled entities, every PII value in a sentence labelled:
- 10 multilingual: non-ASCII names and places; two sentences in Spanish and French.
- 8 street addresses (labelled `LOCATION`, the full street part).
- 12 obfuscated identifiers: spelled-out separators, spaces, dots, upper case.
- 10 ambiguous: names that are common words, places named like people.
- 20 negatives: look-alikes with no PII (version numbers, order and tracking numbers, dates,
  error codes) and plain support questions.

By type: LOCATION 30, PERSON 18, PHONE_NUMBER 4, EMAIL_ADDRESS 3, CREDIT_CARD 3, US_SSN 2,
IP_ADDRESS 2. **Disclosure:** the agent wrote the set after seeing some of the scrubber's
behaviour that day, so it is not blind. Some negatives are judgement calls: `404-555-1000` is
phone-shaped but labelled an error code, and `1234567812345678` is an order number that may pass a
Luhn check. The scrubber's spaCy model is English-only (`en_core_web_lg`).

## Metrics
- **Redaction recall:** a labelled value is fully covered by a detected span of *any* type, so it
  does not reach the text. This is the privacy measure. Typed recall is reported too.
- **Precision:** detected spans overlapping a labelled entity of the same type, over all detected
  spans of that type. A false positive over-redacts, which hurts usability but not privacy.
- 95% Wilson intervals for every rate. Sample sizes are small, so no rate here generalises.

## Targets (point estimates)
| Measure | Target | Why |
|---|---|---|
| Redaction recall, structured identifiers pooled (email, phone, card, SSN, IP; n = 14) | ≥ 0.90 | A missed identifier leaks it into Kafka, Redis and the model prompt. Per-type n of 2–4 is too small to judge alone |
| Redaction recall, PERSON (n = 18) | ≥ 0.85 | Names are the commonest PII in support chat |
| Redaction recall, LOCATION incl. addresses (n = 30) | ≥ 0.80 | A street address identifies a person; the scrubber has no address recognizer |
| Precision, per detected type | ≥ 0.80 | Over-redaction degrades replies but leaks nothing |
| False positives in the 20 negative sentences | ≤ 3 | Plain support questions should arrive intact |
