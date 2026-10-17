# Task 3 — PII scrubber

**Purpose:** Presidio analyzer + anonymizer with spaCy `en_core_web_lg`, entities PERSON,
EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD, US_SSN, IP_ADDRESS, LOCATION → typed placeholders; async
wrapper runs via `asyncio.to_thread`.
**Inputs:** text. **Outputs:** scrubbed text. Fixture `tests/fixtures/pii_labeled.jsonl` (≥ 30
sentences, PERSON + LOCATION labeled).
**Verify:** unit test on email/phone/card/SSN/IP; A1b script reports per-entity recall and p95 latency
(reported only; the lead sets the floor).
