# Recommendations for a full-score reassessment

Prepared 2026-10-18. Reviewed baseline: `3c63545c8cfd1d04bd0f6bade6d75d6e250cfaaf`.

## Answer and scope

Implementing the earlier recommendations alone does **not** guarantee 100/100. They covered
important product defects, but did not cover every scoring requirement or failure mode.
This document replaces that short checklist with explicit completion criteria.

Per the request, disregard the challenge deadline, build duration, and whether improvements
arrived later. Evaluate the completed project at a newly selected commit. The previous 73/100
was a sampled assessment of a different snapshot; it is not a score for this baseline.

**Full-credit target:** every criterion below passes, supporting evidence matches the selected
commit, and an independent review finds no unresolved defect that warrants a deduction under the
agreed rubric. This is a route to demonstrating full credit, not a promise of a particular future
score or proof that a distributed system is flawless. No retrospective test run establishes AI
authorship; use authentic evidence for authorship claims or state that it is unavailable.

The baseline already contains acceptance tracking, persistent owner bindings, a refund Lua script,
checkpoint replay handling, persistence configuration, expanded CI, and live outage/worker-kill
tests. Their presence is source evidence, not a fresh passing result. Verify and extend them;
do not rebuild features merely because the earlier snapshot lacked them.

## 1. Resolve correctness risks first

### R1. Make refund state and audit guarantees accurate

Source: `services/worker/worker/approval.py`, `tests/integration/test_refund_atomicity.py`.

**Observed at this baseline:** executing the production `_APPLY` script against real Redis with
three disposable keys and a deliberately wrong-type audit key raised `ResponseError`, leaving
the marker present and one refund record present. The audit key remained a string. Probe keys
were deleted afterward; application keys were not modified.

Lua isolation does not provide rollback on runtime errors. The existing test wrapper fails before
or after the whole `EVAL` call; it does not inject failures inside its individual commands.

- Validate script arguments and expected key types before writes; test failures at each internal
  command boundary. Do not claim that prevalidation protects against every possible failure.
- Define an authoritative refund receipt containing the action and necessary audit information.
  Either commit it through a storage mechanism with the required transaction guarantees, or make
  derived stream/audit views idempotently repairable from that receipt. Document the actual
  consistency and recovery guarantees before choosing the implementation.
- An existing marker must prove a valid receipt exists. Conflicting or incomplete state must
  trigger repair or an explicit error; it must never produce a false success message.
- Preserve at-most-once application across retries, lost replies, restart, restore, and the
  supported retention period. The current action records a refund request, not a payment.

**Completion evidence:** real-store tests cover missing/wrong-type keys, lost acknowledgements,
concurrent approval, restart after each write boundary, and recovery of incomplete state; each
case produces one authoritative request and a verifiable audit outcome, or an explicit failure.

### R2. Verify acceptance through ambiguous delivery

Source: `services/gateway/gateway/app.py`, `producer.py`, and `frontend/src/api.ts`.

- Require broker acknowledgement before returning successful acceptance. A pending claim alone
  must not imply delivery. An outbox is an option, not a mandatory additional service.
- Bind retry identity to the authorized principal, thread, operation, and immutable request.
  Reject conflicting reuse. Preserve the same client message ID after an uncertain outcome.
- Test the separate claim and delivered-marker lifetimes, including staggered expiration and
  retries near retention boundaries; document the supported idempotency window.
- Treat send-timeout and lost-ack outcomes as potentially delivered. Any resulting duplicate
  Kafka records must be harmless through R3, rather than assumed impossible.
- Ensure an audit failure after delivery is recoverable and does not silently erase acceptance
  evidence when a retry takes the already-delivered path.

**Completion evidence:** concurrent POSTs and process termination before send, during send, after
broker acknowledgement, and before/after Redis acknowledgement cannot falsely confirm delivery,
change request identity, or cause an additional logical turn or action.

### R3. Verify replay, checkpoint, and stream recovery together

Source: `worker/consumer.py`, `graph.py`, `streams.py`, `gateway/sse.py`, and the frontend reader.

- Persist enough per-message completion information to recognize older duplicate messages even
  after a later turn changes the thread's latest checkpoint. A latest-message marker alone is
  insufficient evidence for arbitrary historical replay; define the supported replay horizon.
- Exercise termination before/after checkpoint writes, token publication, terminal-event writes,
  dedupe completion, and Kafka offset commits. Verify history as well as terminal-event counts.
- Verify that a reconnected client with an existing Redis cursor and sequence expectation
  completes correctly after recovery. Deleting and recreating a stream is not automatically
  compatible with an already-connected client.
- Preserve stable events, or explicitly version stream attempts and implement client reset and
  reconciliation. Expired or trimmed replay history must produce a defined recovery response.
- Ensure a stale worker cannot write after its lease is lost. Document and test fencing or an
  equivalent exclusion mechanism, including Kafka reassignment and the processing-time bound.

**Completion evidence:** live kill/restart and concurrent/rebalance tests demonstrate one logical
turn and one authorized action, correct ordering, and a coherent client transcript. Include replay
after a subsequent turn and after the advertised retention boundary.

### R4. Close approval and ownership lifecycle edges

- Keep ownership for as long as any related checkpoint, stream, pending action, or replayable
  record can expose data or perform an action. Persistent bindings are acceptable; define safe
  deletion/reuse and storage growth rather than reintroducing an arbitrary expiry.
- Validate authorization on every chat, stream, approval, and recovery path. Test both users
  against live retained checkpoints and token streams, rather than owner-key checks alone.
- A retry of an already-accepted approval must recover its original result even after the pending
  approval key has been removed. Check idempotent acceptance before declaring an approval absent,
  while preserving authorization and conflicting-request validation.
- Define and test approval expiry, competing approve/reject decisions, superseding chat, and
  delayed queued decisions. Enforce the chosen expiry rule where the action is applied.

**Completion evidence:** cross-user data/action access fails; same-request retries remain stable;
conflicting, expired, and superseded decisions have explicit, documented outcomes.

## 2. Demonstrate durability and operational behavior

- State the failure model, recovery-point objective (RPO), recovery-time objective (RTO), retention,
  and backup policy in `SPEC.md` and the relevant ADR. Persistent volumes and append-only files do
  not imply zero data loss on host failure. Reconcile any guarantee with the documented loss window.
- Verify that restore does not independently resurrect work while losing its dedupe, ownership,
  approval, or action receipts. Explain recovery across Kafka and Redis recovery points.
- Run existing persistence, live Qdrant-outage, and live worker-kill tests against fresh images.
  Add only missing failure cases; do not count existing test filenames as passing evidence.
- Re-run scale and latency workloads at declared concurrency, data size, resources, and model.
  Set justified service objectives before judging results; report queue delay, POST latency,
  first visible token, completion latency, errors, and percentiles. Separate mock from real-model
  measurements and verify throughput and recovery, not replica count alone.
- Verify readiness, bounded dependency failure, meaningful error events, DLQ inspection/replay,
  and recovery without silently losing accepted work. Prove observability failure behavior.

**Completion evidence:** snapshot-linked reports give commands, environment, outcomes, failure
injection, recovery checks, and limits. Restrict disruptive tests to a disposable compose project.

## 3. Finish safety and model-quality evaluation

- Evaluate the final routing/refusal/response pipeline, not just top-1 intent accuracy. Use held-out
  benign, attack, paraphrase, and off-topic sets; report false refusals and attack success separately.
  Keep development/tuning data separate from the final evaluation set.
- Broaden PII fixtures beyond easy names and locations: multilingual and ambiguous cases, addresses,
  obfuscated identifiers, and entities split across output chunks. Measure precision and recall by
  entity, disclose sample sizes, and agree on risk-appropriate targets before scoring.
- Test output leakage across the sentence/size buffering boundary and across model deltas. Validate
  prompt separation and action authorization even when heuristic injection detection misses an attack.
- Complete a real local-model answer-quality run with reproducible prompts and transparent scoring.
  Tool installation or a test harness without completed results is not answer-quality evidence.
  Framework choice is secondary to valid measurements; document any departure from the locked plan.
- Exercise the HTTPS user path, default exposed ports, credential handling, authenticated load, and
  sensitive-data handling in logs, traces, and malformed-message DLQ records. Match claims to the
  supported threat model; do not claim complete PII removal or injection prevention from finite tests.

**Completion evidence:** reports identify model/configuration, data split, metrics, targets,
failures, and disposition. Failed safety checks remain visible and are fixed or narrowly scoped
through an explicitly revised contract, not hidden with weaker tests.

## 4. Produce complete QA and delivery evidence

- Run unit, integration, frontend type/build/tests, durability, scale, and real-model checks on the
  selected source revision. Use the required dependencies; identify test doubles explicitly.
- Generate fresh combined backend coverage for that revision, distinguishing unit-only coverage.
  Examine critical missing branches and use branch coverage where helpful. A high percentage alone
  does not demonstrate crash safety, and 100% coverage is not a requirement of this rubric.
- Fix the React test environment warnings. Add meaningful browser verification of authentication,
  streaming interruption/recovery, approval retry, and error states against the actual gateway.
- Run reproducible checks in CI; attach logs and reports to their source SHA. Heavy model/load jobs
  can be controlled release checks rather than required on every push, provided the evaluated
  revision has the completed evidence. Do not label an unexecuted marker as covered.
- Complete README run/test/deployment instructions and a fresh-clone walkthrough. Keep engineering
  rationale in ADRs. Reconcile `SPEC.md`, HLD/LLD, limitations, and reports with implemented behavior.
- Update `docs/summary.md` with what works, measured limits, deployment status, residual risks, and
  the next decision. Identify historical reports and stale coverage instead of presenting them as
  current. The existence of the summary is already established; accuracy is the remaining check.

## 5. Full-credit evidence matrix

Use the attached rubric's dimensions, with deadline and elapsed-build-time restrictions waived by
the requester. Authenticity of evidence still matters. Score the new revision, not the old score
plus a fixed number of points for each completed task.

| Criterion | Max | Evidence required to justify full credit |
|---|---:|---|
| Design documents | 15 | Accurate diagrams, constraints, alternatives, failure/recovery and scaling analysis; claims spot-checked against implementation and tests. |
| Design-agent evidence | 10 | Authentic named-agent design artifacts with multiple proposal/decision refinements and corroborating source/PR evidence; no unsupported authorship claims. |
| Robustness | 20 | Consistent error handling and structured logs, durable/queryable action audit, demonstrated failure/recovery behavior, and no unresolved confirmed data-integrity defect in reviewed scope. |
| Development evidence | 15 | Specific changes and decisions linked to authentic agent sessions/runs and PRs; meaningful agent-configuration changes explained; evidence quality rather than speed or commit volume. |
| Tests | 15 | Meaningful doubles, real-dependency checks, primary/edge/failure paths, fresh snapshot-matching coverage, and passing required checks. |
| AI/automated QA evidence | 10 | Authentic AI test-authorship evidence linked to successful server-anchored automated execution and the revision's reports. CI alone does not establish AI authorship. |
| Leadership handoff | 8 | Concise, decision-relevant, accurate summary with verified figures, tradeoffs, remaining risks, and deployment status. |
| Orchestration narrative | 7 | Specific stage-by-stage account with authentic model/session attribution and corroborated factual claims; unavailable evidence disclosed. |
| **Total available** | **100** | **All criteria independently assessed at the completed revision.** |

Preserve existing transcripts, session/run identifiers, diffs, and CI artifacts where available.
Do not fabricate missing transcripts or retrofit claims of authorship. Missing authentic evidence
may limit process-related points even if the product is excellent. Build duration is not scored.

## 6. Final acceptance procedure

1. Review the current implementation against R1-R4 and write a precise regression for each remaining
   failure. Keep already-correct behavior and tests.
2. Resolve correctness and recovery issues, then complete operational, safety, and model evaluation.
3. Reconcile contracts and handoff; run the complete required verification on the final source
   revision and publish its reports without changing the source under test.
4. Have a reviewer reassess all eight criteria, verify report-to-source linkage, cite strengths for
   points above each rubric band floor, and deduct for observed defects or unsupported claims.
5. Award 100/100 only if that review supports every point. If it does not, report the exact unmet
   criterion and evidence needed; never promise a full score merely because this checklist is done.

This document is a recommendation and verification plan. Only the isolated R1 Redis probe was
executed for this update; it does not claim the current project's full suite, deployments, or
model-quality evaluations pass. The earlier observed test results belong to the five-hour snapshot.
