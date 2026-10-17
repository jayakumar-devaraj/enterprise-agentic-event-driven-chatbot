# Task 7 — Worker consumer

**Purpose:** consumer-group loop, manual commit only after a durable outcome; dedupe lease
(`processing` NX EX lease → `done` TTL; delete on terminal failure; `done` ⇒ skip+commit;
`processing` ⇒ no commit); bounded retries with backoff for infra faults → ErrorEvent + DLQ + commit;
malformed envelope → DLQ, no retry.
**Verify:** A4 (malformed → DLQ) and A5 (duplicate → one `done`; abandoned lease → reprocessed after
expiry) integration tests against real Kafka + Redis.
