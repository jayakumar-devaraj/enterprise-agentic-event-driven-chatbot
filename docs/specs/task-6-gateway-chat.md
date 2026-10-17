# Task 6 — Gateway POST /chat

**Purpose:** HTTP Basic auth (`AUTH_USERS`, scrypt) → 401; thread ownership `thread_owner:{thread_id}`
SET NX → 403; Pydantic 422; 413 over `MAX_MESSAGE_CHARS`; Redis per-thread rate limit 429; repeated
`message_id` returns the same 202 body; Presidio scrub off-loop; produce envelope keyed by `thread_id`;
503 when Kafka is unavailable; CORS allowlist; audit `chat_accepted`.
**Verify:** unit tests with test doubles for each status code; A1 integration: consumed Kafka record
contains placeholders and none of the raw values.
