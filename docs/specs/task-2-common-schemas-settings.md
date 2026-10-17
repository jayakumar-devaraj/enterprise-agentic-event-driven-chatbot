# Task 2 — Common schemas, settings, logging, audit

**Purpose:** Pydantic v2 models defined once (`ChatRequest`, `ChatAccepted`, envelope, `TokenEvent`,
`DoneEvent`, `ErrorEvent`, `ApprovalRequiredEvent`, `ApprovalDecision`); `Settings` from env with the
dedupe-lease formula validator; JSON log formatter; audit writer to Redis stream `audit:events`.
**Inputs:** env. **Outputs:** `chat_common.{schemas,settings,logjson,audit}`.
**Verify (unit):** valid/invalid `thread_id` cases; lease below the formula raises (A5 unit part);
log lines are valid JSON and carry `trace_id`; audit writer XADDs the expected fields (fake Redis
test double).
