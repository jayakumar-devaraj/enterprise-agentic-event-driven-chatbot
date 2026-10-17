# Known limitations

Appended whenever scope is cut, a shortcut is taken, or something is left unverified.

- **Cut at Step B (time):** Tier 3 (Caddy TLS/HTTP2, A11 latency), A10 horizontal-scale test, React
  client + A8, DeepEval answer-quality eval + A9 real Ollama round trip, service container images +
  full-compose end-to-end + A4 Qdrant-stopped case. Recorded in `docs/master_plan.md`.
- **Access control over plain HTTP:** HTTP Basic credentials travel in cleartext until a TLS proxy
  exists (Tier 3, cut).
- **LangFuse Cloud egress:** scrubbed prompts and outputs leave the machine to US LangFuse Cloud;
  runtime is no longer fully offline.
- **Restored (10:58 CDT, lead):** React client + A8 (Task 14); deadline moved to 17:40 CDT. Node is
  not installed on the build machine, so the frontend builds and tests inside `node:24.22.0-slim`.
- **A1b fixture is easy (Task 3):** `tests/fixtures/pii_labeled.jsonl` was written by the agent with
  common Western names and well-known cities; measured recall 1.0 PERSON (32/32) and 1.0 LOCATION
  (36/36), scrub p95 5.72 ms on the build machine (`docs/reports/pii_recall.json`). This overstates
  real-world recall; no recall floor has been set (the lead sets it).
- **Presidio sample-SSN rule (Task 3):** Presidio deliberately ignores published sample SSNs
  (`123-45-6789`, `987-65-4320`, `078-05-1120`); `123-45-6789` passes through unscrubbed, and
  `078-05-1120` is caught only as `PHONE_NUMBER`.
- **Scrubber false positive (Task 3):** a sentence-initial "Email" was tagged `PERSON` and replaced.
