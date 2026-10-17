# Task 11 — Observability

**Purpose:** OpenTelemetry spans gateway → worker (context via Kafka header or envelope `trace_id` per
spike b) exported OTLP to Jaeger v2; LangFuse Cloud spans for LLM calls with scrubbed input/output,
route scores, engine tag.
**Verify:** integration test finds the trace for one request in Jaeger's query API with both services;
LangFuse ingestion call succeeds with the `.env` keys (UNVERIFIED if keys/network unavailable).
