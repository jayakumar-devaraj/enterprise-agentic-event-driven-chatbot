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
- **Previously cut, now built (17:07–19:20):** container images + one-command stack (Task 12), A9,
  A10, Caddy TLS and A11 — see the reports in `docs/reports/`. Still not automated: A4's live
  "Qdrant stopped after startup" case (the dependency-failure path is covered with test doubles).
- **Phase 2.5 cut short:** items 3–5 (extra criterion tests, plan/spec reconciliation, fixing
  limitations) were not done; time reached the Phase 3 floor after a CI wait of about 1h50m
  (15:05–16:55 CDT) while merging PR #17. `docs/master_plan.md` and `docs/specs/` were not
  reconciled line by line after the design changes recorded in ADRs 0002–0007.
- **CLAUDE.md §9 "Open risk" still present:** spike (a) resolved it (ADR 0002), but deleting the
  block needs the lead's permission (asked, not yet answered).
- **`worker.main` has no automated test** (0 % in `docs/reports/coverage.txt`); it is exercised by
  the containerized runs (`docs/reports/e2e-containers.md`, A9, A10).
- **Single-turn prompts to the real LLM** include only the last 6 history turns.
- **Small-model echo (A9):** `llama3.2:1b` repeats the `<user_message>…</user_message>` block (the
  user's own, already scrubbed text) at the start of its reply. Harmless for privacy, poor UX; a
  larger model or stripping echoed delimiters in the output guard would fix it.
- **TLS (Tier 3, done 19:06):** Caddy now serves https://localhost:8443 with an internal CA. Basic
  credentials are still cleartext on the direct dev ports (gateway 8000–8003, Vite 5173), which remain
  published on 127.0.0.1 for development; browsers must trust Caddy's local CA or accept the warning.
- **Latency (review, 20:07):** worker CPU is the bottleneck (both replicas at their 2-CPU cap under
  20 concurrent users) and the async Kafka producer adds up to ~1 s per POST through its default
  1 s Python-side buffer. One fix attempt broke delivery and was reverted; next steps are in
  `docs/reports/a11-latency.md`.
- **Review items not done before the 20:30 stop:** an automated A4 "Qdrant stopped while running" test; publishing only Caddy by
  default (moving dev ports 8000–8003 / 5173 to an opt-in override). The injection false positive
  needs a lead decision (the rules forbid tuning the threshold or seed data to pass a case).
- **Small-model echo — fixed (20:11):** the output guard now drops echoed `<user_message>` /
  `<history>` blocks before scrubbing (`tests/unit/test_llm_guards.py`); not re-run against Ollama.
- **Resolved after the 20:30 stop (21:16, PR #30):** the CLAUDE.md §9 "Open risk" block was deleted
  with the lead's permission; the entry above about it is superseded.
- **Demo script (`scripts/end_to_end_demo.py`, post-deadline):** verified on Windows 11 only (CMD,
  PowerShell, Git Bash); macOS not run. The Ctrl+C path (exit 130) was not exercised. Python on
  Windows reports `NUL` as a terminal, so with `< NUL` the prompt text prints before the EOF fallback
  picks the default.
- **Audit remediation (2026-10-17, `docs/reports/project-audit-2026-10-17.md`):** thread ownership
  no longer expires (P1); `thread_owner:*` keys now grow by one short string per thread. 202 is
  returned only after the broker ack, and an unacknowledged claim is re-produced on retry (P1);
  concurrent repeats may produce twice (the worker dedupes) and so may audit `chat_accepted` twice.
  A retried `/approve` after the worker consumed the decision got 404, not the original 202 (fixed
  2026-10-18: it now returns the original 202).
  Refund marker, request and audit entry are one atomic Lua script with a non-expiring marker (P1);
  the three keys must share a hash slot before any move to Redis Cluster. Redelivery after a crash
  no longer duplicates the history turn or deletes a finished stream (P1); a new chat during a
  pending refund now clears it and audits `refund_abandoned` (supersedes the Task 10 entry). These
  crashes are simulated in-process (`tests/integration/test_replay_recovery.py`), not by killing a
  worker container. A crash after the interrupt checkpoint may audit `refund_proposed` twice.
  Redis now persists (AOF `everysec`) and Kafka writes to a named volume instead of `/tmp` (P1,
  ADR 0008); a host crash can still lose ≤ 1 s of Redis writes, Kafka has no replica and is not
  backed up. Restore was tested on a disposable compose project, not on a production host.
- **Injection detector measured on a held-out set (2026-10-17, ADR 0004 addendum):** the current
  rule refuses 10/20 benign held-out messages (mostly refund and money-back phrasings) and detects
  12/20 novel injections. The pre-registered margin rule (2/20 refused, 9/20 detected) missed its
  targets and was not adopted. The structural defence and the human refund gate are unaffected.
- **Answer-quality evaluation not yet run (2026-10-18, ADR 0009):** the DeepEval harness is on branch
  `feat/deepeval-answer-quality` (unmerged). Its first full run failed because each local-judge call
  exceeded DeepEval's 88.5 s per-attempt limit on CPU; no quality score exists yet. Paused by the lead.
- **Contrastive injection detector adopted below its targets (2026-10-18, ADR 0004/0006):** on the
  fresh holdout it refuses 4/20 benign messages and detects 20/20 injections; it also refuses 3
  off-topic `intents.eval` requests (no in-scope one). The seeds and holdout were written by the same
  agent, so the holdout measures generalisation across phrasings by one author only. Supersedes the
  Task 5 false-positive entry and the 2026-10-17 detector entry above.
- **A4 live Qdrant outage automated (2026-10-18):** `tests/durability/test_dependency_outage.py` stops
  the real Qdrant container under the production worker, graph and router; supersedes the "not
  automated" A4 entries above.
- **A5 live worker kill automated (2026-10-18):** `tests/durability/test_worker_kill.py` SIGKILLs the
  real worker container 3 times while 300 messages on 6 threads are in flight and checks one `done`
  per message and one history turn per message per thread. Kill points are timing-based, not
  targeted at a specific boundary; the targeted boundaries are covered in-process by
  `tests/integration/test_replay_recovery.py`.
- **Output-guard chunk leak fixed (2026-10-18):** the guard flushed at any `.`, so an email, dotted
  phone or IP streamed in pieces reached the client unscrubbed, and the 200-char forced flush could
  split a card number. Sentence ends now need trailing whitespace and forced cuts fall between
  lowercase words (`tests/unit/test_llm_guards.py`). Residual risk is described in LLD §10.
- **Refund script partial write (2026-10-18, R1 of `docs/reports/full-score-recommendations.md`):**
  Redis does not roll back a Lua script on a runtime error. A wrong-type `audit:events` key left the
  marker and the refund record written with no audit entry. The script now checks argument and key
  types before its first write and sets the marker last, holding its `refunds` entry id
  (`tests/integration/test_refund_atomicity.py`). Residual risk: the audit stream is trimmed by
  `MAXLEN`, so an old marker can be checked against its `refunds` entry but not its audit entry.
  Markers written before this change hold `1` and are trusted without that check.
- **Harder PII set measured (2026-10-18):** `tests/fixtures/pii_hard.jsonl` (30 sentences, all seven
  entity types; written by the agent) recalls 33/38 (86.8 %): IP, phone and SSN 100 %, card 3/4,
  email 4/5, location 6/7, person 8/10. Misses: obfuscated email and spelled-out card, a lowercase
  name, a name of common words ("Hope Summers"), "Kraków" (`docs/reports/pii_recall_hard.json`). No
  recall floor is set; the lead sets one.
- **Stale worker after a lost lease (2026-10-18, R3 of `docs/reports/full-score-recommendations.md`):**
  a worker that stalled past `DEDUPE_LEASE_S` while another took the message over still wrote when
  it resumed. Observed on real Redis: it appended a second copy of the reply after the new owner's
  `done` and committed. Stream, `done`/release and refund writes are now fenced by the lease token
  (ADR 0009, `test_stale_worker_cannot_write_after_another_took_over`). Not fenced atomically:
  LangGraph checkpoint writes, `pending_approval:*` and audit entries. A stale worker reaches them
  only if it stalls for a full lease between two adjacent awaits, and can then duplicate an audit
  entry or replace a checkpoint. The DLQ produce is preceded by a non-atomic lease check.
- **Expired streams (2026-10-19, R3 of `docs/reports/full-score-recommendations.md`):** opening or
  resuming a stream that had expired used to wait silently for up to `STREAM_TTL_S` (1 h) and then
  close with no terminal event. A finished message now gets an immediate `stream_expired` error
  event, and a message that was never accepted gets 404 (SPEC §6). Residual: a message that failed
  (DLQ, dedupe key deleted) and whose stream has also expired cannot be told apart from a queued
  one, so it still waits out the window. The finished reply itself is not retrievable once its
  stream expires; no history endpoint exists.
- **Rebalance under load automated (2026-10-19, R3 of `docs/reports/full-score-recommendations.md`):**
  `test_rebalance_while_processing_loses_and_duplicates_nothing` in
  `tests/durability/test_worker_kill.py` produces 600 messages on 12 threads in four batches, one
  right before each step: scale the worker 1 → 3, SIGKILL one replica, scale to 2, back to 1. At
  least two steps must leave work unfinished when they return. It then requires one `done` with
  contiguous `seq` per message, and every thread's turns in order with one pair per message. A first
  version with one up-front batch passed locally (629 s), but failed its guard in one CI run because
  the runner drained every message before the second step (the correctness checks still passed). The
  batched version is verified in CI only: a local rerun was stopped for low host memory. Not shown:
  whether a fenced write was rejected during the run (the project's logs are removed with it), or
  that the test fails on the code before ADR 0009. Step timing is wall-clock based, like the kill
  test.
- **Approval rules enforced where applied (2026-10-23, R4 of
  `docs/reports/full-score-recommendations.md`):** before this change, the gateway alone checked the
  approval window and the decider's identity. On real Redis, the worker applied a refund for a
  decision accepted an hour after the window, and for a decision carrying another user's id. The
  worker now refuses both (`approval_expired`; `thread_owner_mismatch` since 2026-10-23, which replaced
  `approval_forbidden` once the worker checked ownership on every envelope; SPEC §10). Residuals: an
  expired decision leaves the refund paused in the checkpoint, so the next chat on the thread also
  audits it as `refund_abandoned`. A refund paused before `proposed_at` existed counts as expired.
  The check compares the gateway's and the worker's clocks, which are the same host clock under
  compose but would need synchronised clocks on separate hosts.
- **DLQ replay tool (2026-10-23, section 2 of `docs/reports/full-score-recommendations.md`):**
  `python -m worker.dlq` lists and replays dead-lettered envelopes. A naive re-produce was shown on
  real Kafka and Redis to be dead-lettered again without processing, because the failed attempt's
  `dependency_unavailable` stream read as an outcome already recorded. Replay therefore deletes that
  stream first. Caveats: a replayed message joins its thread when it is replayed, after any later
  turns. A replayed approval keeps its original acceptance time, so one replayed after the approval
  window gets `approval_expired`. DLQ offsets are unambiguous only while the DLQ has one partition
  (as `kafka-init` creates it).
- **Latency rerun fails every latency target (2026-10-24, `docs/reports/latency-2026-10-24.md`):**
  against targets pre-registered before the run (proposals for the lead), the mock run of record
  (200 turns, 20 in flight) had 9 `POST 503`s, POST p95 6.3 s, queue delay p95 44.7 s and
  completion p95 46.8 s at 1.8 turns/s. The real model (20 turns, 2 in flight) had no errors and a
  completion p95 of 33.1 s, but a first-token p95 of 27.2 s and 2 empty replies. The gateway's Kafka
  produce takes about 2 s even in isolation, and times out (503) inside the loaded gateway. Worker
  CPU outside traced spans and the queue delay are unexplained. No fix attempted; next experiments
  are in the report.
  **Update, same day:** the produce floor and the 503s were fixed (`AIOProducer` unbatched with a
  delivery-report task, ADR 0003 addendum). On the same load, mock errors went 9 → 0 and steady-state
  POST p95 dropped to 181 ms. The first 20 requests after a gateway restart still take about 6–7 s
  (cold start, cause unknown), so POST p95 over the whole run still fails. Worker-side queue delay
  (p95 8.8 s) and real-model first-token latency still fail. The report's percentiles were
  recomputed with the inclusive method, since the exclusive default extrapolated small samples.
- **Output guard leaked at its hard limit (2026-10-24, section 3 of
  `docs/reports/full-score-recommendations.md`):** with 1000 buffered characters, no sentence end and
  no gap between two lowercase words, the guard flushed the whole buffer. If the latest model delta
  stopped mid-entity, each half was scrubbed alone and neither was recognised. In tests an email,
  a card number and a phone number came out in clear. The LLD had listed this as a residual risk. The
  guard now emits `[redacted]` and ends the reply in that case, so a legitimate 1000-character run
  of digits or capitals is cut off. Tested and not a leak: an entity separated from its context by a
  newline chunk boundary (SSN, phone, card, email, IP, person, location). Not covered: an echo of a
  system prompt that omits its first 40 characters (the prompts hold no secrets).
- **Raw PII in DLQ records and logs (2026-10-25, section 3 of
  `docs/reports/full-score-recommendations.md`):** a scan of the running stack
  (`tests/stack/test_sensitive_data.py`, `-m stack`) found the gateway path clean in every sink:
  SSE stream, container logs, `chat-requests`, the DLQ, Redis and the Jaeger trace. A malformed record
  sent straight to Kafka was different: its DLQ record kept the raw bytes (base64 in `original_b64`,
  plus the validation error text), and the worker log quoted part of the input. The worker now keeps
  only a SHA-256, the length and the error types and field locations. Not covered: names and
  locations, because a random name cannot be relied on to be recognised; LangFuse, which was
  stopped; host-level logs outside Docker.
- **Broad PII set misses its recall targets (2026-10-25, `docs/reports/pii-broad-2026-10-25.md`):**
  on 60 agent-written sentences with targets pre-registered before scoring, structured identifiers
  were redacted 10/14 (target 0.90) and locations 16/30 (target 0.80). Persons passed at 16/18.
  Precision failed for PERSON (0.72) and IP (2 spans). No street address was redacted, obfuscated
  emails, cards and IPs leaked, and place names in non-English sentences were missed. No fix has
  been tuned on this set; candidate recognizers need a fresh held-out set.
