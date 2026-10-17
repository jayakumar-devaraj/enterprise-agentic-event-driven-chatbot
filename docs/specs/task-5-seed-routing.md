# Task 5 — Seed and routing thresholds

**Purpose:** `data/intents.seed.jsonl` (≥ 10 per billing/tech_support/general), `data/intents.eval.jsonl`
(≥ 3 per intent + ≥ 5 off-topic, never loaded), `data/injection.seed.jsonl`; seed service loads
`intents` and `injection_patterns` with passage prefixes, fails loud if model/collection missing;
leave-one-out sets `ROUTE_THRESHOLD` (and `INJECTION_THRESHOLD`), recorded in ADR 0004.
**Verify:** A2 integration test: every eval sample routes to its label, off-topic → `general`, against
real Qdrant.
