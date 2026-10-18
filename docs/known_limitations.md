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
- **Injection-detection false positive (Task 5):** with `INJECTION_THRESHOLD=0.6113` (ADR 0004) the
  benign eval sentence "How do I get my money back for this charge?" scores 0.658 and would be
  routed to the refusal node. Detection is heuristic; structural prompt separation remains the
  primary defence. Not tuned away.
- **Unexplained slow run (Task 9):** one local unit-suite run reported 2081 s (13:40–14:12 CDT); an
  immediate rerun took 13.5 s and no test exceeded 2.2 s. Cause not identified (host stall
  suspected); about 30 minutes of the budget were lost.
- **Approval gate edge (Task 10):** sending a new chat message on a thread while a refund awaits
  approval starts a new graph run and abandons the paused one; a later approve then gets
  `ErrorEvent{approval_not_pending}` and no refund is applied (fails safe, but the user must ask
  again). The refund rule is a keyword match on "refund(s)" in billing-routed text.
- **Not built (cut, final state):** gateway/worker/seed container images and a one-command full
  stack (Task 12; services run as host processes, the frontend runs in compose); A9 real Ollama
  round trip (engine and compose profile exist, untested); A10 horizontal-scale test; Tier 3 Caddy
  TLS and A11 latency report.
- **Phase 2.5 cut short:** items 3–5 (extra criterion tests, plan/spec reconciliation, fixing
  limitations) were not done; time reached the Phase 3 floor after a CI wait of about 1h50m
  (15:05–16:55 CDT) while merging PR #17. `docs/master_plan.md` and `docs/specs/` were not
  reconciled line by line after the design changes recorded in ADRs 0002–0007.
- **CLAUDE.md §9 "Open risk" still present:** spike (a) resolved it (ADR 0002), but deleting the
  block needs the lead's permission (asked, not yet answered).
- **`worker.main` has no automated test** (0 % in `docs/reports/coverage.txt`); it was exercised by
  the process smoke runs in PR #11 and `docs/reports/e2e-observability.md`.
- **Single-turn prompts to the real LLM** include only the last 6 history turns.
- **Small-model echo (A9):** `llama3.2:1b` repeats the `<user_message>…</user_message>` block (the
  user's own, already scrubbed text) at the start of its reply. Harmless for privacy, poor UX; a
  larger model or stripping echoed delimiters in the output guard would fix it.
- **TLS (Tier 3, done 19:06):** Caddy now serves https://localhost:8443 with an internal CA. Basic
  credentials are still cleartext on the direct dev ports (gateway 8000–8003, Vite 5173), which remain
  published on 127.0.0.1 for development; browsers must trust Caddy's local CA or accept the warning.
