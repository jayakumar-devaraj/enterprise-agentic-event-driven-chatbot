# AI orchestration

Build-process record (not the runtime audit trail). Agent: Claude Code, model `claude-opus-5-5`,
sessions listed per row. Lead: Jayakumar Devaraj (gates and decisions).
| task | generated (code/tests/docs) | branch | PR # | session |
|---|---|---|---|---|
| 0 persist plan | docs: master_plan, specs 1–11, design_dialogue, known_limitations, this log | plan/setup | #1 | 25d36d33 |
| 0b design docs | docs: HLD, LLD, ADR 0001/0005/0006/0007, master_plan update | task-0b-design-docs | #2 | 25d36d33 |
| 1 workspace skeleton | code: uv workspace, CI; tests: workspace import smoke; docs: SPEC, README, .env.example, CLAUDE.md §9 | task-1-workspace-skeleton | #3 | 25d36d33 |
| 0c design update | docs: React chat window + Qdrant gRPC in HLD/LLD, ADR 0006 rows; config keys | task-0c-design-react-grpc | #4 | 25d36d33 |
| 2 common schemas/settings | code: schemas, settings, logjson, audit; tests: 25 unit + 1 integration (real Redis) | task-2-common-schemas-settings | #5 | 25d36d33 |
| 3 PII scrubber | code: scrubber; tests: 9 scrubber/harness unit; eval: A1b recall report; docs: limitations | task-3-pii-scrubber | #6 | 25d36d33 |
| 4 compose infra + spikes | code: deploy/compose.yaml, ollama Dockerfile; tests: 3 spike/audit integration (real Kafka, Redis); docs: ADR 0002, 0003 | task-4-compose-infra-spikes | #7 | 25d36d33 |
| 5 seed + routing | code: seed main/calibrate, worker embedding/router; data: seed/eval/injection; tests: 5 unit + A2 integration (real Qdrant gRPC); docs: ADR 0004 | task-5-seed-routing | #8 | 25d36d33 |
| 6 gateway POST /chat | code: gateway app/auth/producer; tests: 12 unit (doubles) + A1 integration (real Kafka, Redis) | task-6-gateway-chat | #9 | 25d36d33 |
| 7 worker consumer | code: worker consumer/streams, compose IPv4 listener; tests: 5 unit (doubles) + A4, A5×2 integration (real Kafka, Redis) | task-7-worker-consumer | #10 | 25d36d33 |
| 8 LangGraph pipeline | code: worker graph/llm/guards/main; tests: 5 unit + A6, A7×2 integration (real Redis checkpointer); process smoke | task-8-langgraph-graph | #11 | 25d36d33 |
| 9 token stream + SSE | code: gateway sse relay + stream endpoint; tests: A3 + owner/422 integration (real Redis, real graph) | task-9-token-stream-sse | #12 | 25d36d33 |
| 10 approval gate | code: worker approval/graph interrupt, gateway /approve; tests: 1 unit + 3 integration (real Redis) | task-10-approval-gate | #13 | 25d36d33 |
| 11 observability | code: chat_common.tracing, gateway instrumentation, worker spans + LangFuse observer; tests: Jaeger integration; docs: e2e report (rung 3), ADR 0003 note | task-11-observability | #14 | 25d36d33 |
| fix kafka CPU | config: Kafka TCP healthcheck, kafka-init readiness wait; verified CPU samples + 18 integration tests | fix-kafka-healthcheck-cpu | #15 | 25d36d33 |
| 14 React chat window | code: frontend app + Dockerfile, compose service; tests: A8 (5 Vitest) | task-14-react-chat-window | #16 | 25d36d33 |
| H1 CI integration + frontend | config: CI jobs integration (compose) and frontend; verified run 39065303808 | hardening/ci-integration | #17 | unrecorded |
| wrapup | docs: README sections, demo script, summary, coverage report, limitations, this narrative | wrapup | #18 | 25d36d33 |
| 12 service images (WIP push) | code: gateway/worker/seed Dockerfiles, compose services; merged as PR #20 by another session before e2e verification | task-12-service-images | #20 | unrecorded |
| compose resource caps | config: memory/CPU caps, Kafka heap; authored and merged in another session after Docker Desktop crashed (not this session's work) | (other session) | #19 | unrecorded |
| 12 verification | docs: `docs/reports/e2e-containers.md` (containerized e2e verified after the fact) | task-12-verification | #21 | 25d36d33 |
| 15 A10 scale | tests: tests/load/test_scale_a10.py (marker `scale`); docs: docs/reports/a10-scale.md | task-15-scale-a10 | #22 | 25d36d33 |
| 13 A9 real Ollama | code: gateway Redis socket timeout fix + regression test, Ollama Dockerfile PID fix, worker MOCK_LLM env; tests: tests/load/test_ollama_a9.py (marker `llm`); docs: a9 report | task-13-ollama-a9 | #23 | 25d36d33 |
| T3 Caddy TLS | config: deploy/caddy/Caddyfile, compose caddy service; docs: tier3 report | tier3-caddy-tls | #24 | 25d36d33 |
| T3 A11 latency | tests: tests/load/latency_a11.py; docs: a11 report + json | tier3-latency-a11 | #25 | 25d36d33 |
| reconcile + docs | docs: coverage re-measured (88 %), master plan/specs/limitations/summary/README reconciled | reconcile-docs | #26 | 25d36d33 |
| review findings | docs: A11 investigation (worker CPU bottleneck, AIOProducer 1 s buffer; reverted fix attempt) | fix-review-items | #27 | 25d36d33 |
| echo fix | code: output guard drops echoed delimiter blocks; tests: 1 unit | fix-model-echo | #28 | 25d36d33 |
| final consistency | docs: coverage re-measured at `c3b4f0f` (88 %, 86 passed), summary figures, PR-number mapping below | final-consistency | #29 | 25d36d33 |
| remove open risk | docs: CLAUDE.md §9 open-risk block deleted (lead approved, after the 20:30 stop); resolved by `tests/integration/test_spike_checkpointer.py`, PR #7 | remove-open-risk | #30 | 25d36d33 |
| docs refresh (post-deadline) | docs: README project description; stale claims fixed in summary, HLD, LLD, ADR 0006/0007, master_plan, a11 report, limitations | docs-refresh | #31 | 25d36d33 |
| demo script phase 1 | code: `scripts/end_to_end_demo.py` (POST /chat + SSE, stdlib only); verified live: argument, prompt, unreachable, 401 | demo-script-phase-1 | #33 | 25d36d33 |
| demo script phase 2 | code: demo finds/starts the compose stack, Docker checks, one-line failures, non-tty input; verified warm, cold, Docker unreachable/missing, twice in a row, NUL stdin | demo-script-phase-2 | #34 | 25d36d33 |
| demo script phase 3 | code: stop/ps hints use the absolute compose path; verified on Windows from CMD, PowerShell, Git Bash, another directory, cold start, printed stop command; macOS not run | demo-script-phase-3 | #35 | 25d36d33 |
| Keep thread ownership for the life of the thread's data | files changed (by path): code 1, docs 4, other 1, tests 2 | fix/ownership-lifetime | #36 | 9d3720f9 |
| Return 202 only for broker-acknowledged work and keep retry identity | files changed (by path): code 1, docs 4, other 3, tests 3 | fix/acceptance-protocol | #37 | 9d3720f9 |
| Record refund marker, request and audit entry atomically | files changed (by path): code 4, docs 3, other 2, tests 1 | fix/refund-atomicity | #38 | 9d3720f9 |
| Make redelivery advance a thread by at most one turn | files changed (by path): code 3, docs 4, other 4, tests 2 | fix/replay-idempotency | #39 | 9d3720f9 |
| Persist Redis and Kafka state on named volumes | files changed (by path): docs 4, other 4, tests 1 | fix/redis-kafka-persistence | #40 | 9d3720f9 |
| Verify passwords off the event loop | files changed (by path): code 1, docs 3, other 1, tests 1 | fix/auth-off-event-loop | #41 | 9d3720f9 |
| Separate liveness from readiness and check Kafka | files changed (by path): code 3, docs 2, other 2, tests 3 | fix/readiness-probe | #42 | 9d3720f9 |
| Measure the refusal rules on a held-out guard set (candidate not adopted) | files changed (by path): docs 4, other 1, tests 1 | fix/refusal-false-positive | #43 | 9d3720f9 |
| Detect injections by contrastive margin against benign exemplars | files changed (by path): code 5, docs 9, other 6, tests 3 | feat/contrastive-injection-guard | #44 | 9d3720f9 |
| Reconcile the summary, LLD and limitations with the audited fixes | files changed (by path): docs 4 | docs/reconcile-after-audit | #45 | 9d3720f9 |
| Automate A4 with a live Qdrant outage | files changed (by path): docs 2, other 1, tests 1 | test/a4-live-qdrant-outage | #46 | 9d3720f9 |
| Automate A5 with a live worker SIGKILL under load | files changed (by path): docs 3, other 1, tests 2 | test/a5-worker-kill | #47 | 9d3720f9 |
| Wait for Qdrant to serve before the A4 recovery message | files changed (by path): tests 1 | fix/a4-wait-qdrant-serving | #48 | 9d3720f9 |
| Stop the output guard from flushing entities in fragments | files changed (by path): code 1, docs 3, tests 1 | test/pii-hard-cases | #49 | 9d3720f9 |
| Document remaining requirements for full-score reassessment | files changed (by path): docs 1 | codex/evaluation-remediation-checklist | #50 | unrecorded |
| Measure PII recall on a harder labelled set | files changed (by path): docs 5, tests 3 | test/pii-hard-fixture | #51 | 9d3720f9 |
| Replace the coverage report with the CI report and log recent PRs | files changed (by path): docs 4 | docs/freeze-figures | #81 | 9c652407 |
| Bind the delivered marker to the request so acceptance retries stay idempotent | files changed (by path): code 1, other 1, docs 2, tests 2 | fix/acceptance-binding | #52 | 2d000a26 |
| Validate refund script inputs before writing and make the marker a receipt | files changed (by path): code 1, other 1, docs 2, tests 1 | fix/refund-receipt | #53 | 2d000a26 |
| Fix Mermaid diagrams that GitHub cannot render | files changed (by path): docs 2 | fix/mermaid-diagrams | #54 | 2d000a26 |
| Fence writes made under the dedupe lease with a per-attempt token | files changed (by path): code 5, other 1, docs 4, tests 7 | fix/lease-fencing | #55 | 2d000a26 |
| Make the dedupe window outlive Kafka retention so replays are always recognised | files changed (by path): code 1, other 3, docs 2, tests 2 | fix/replay-horizon | #56 | 2d000a26 |
| End an expired stream at once with stream_expired and 404 unknown messages | files changed (by path): code 3, other 1, docs 2, tests 2 | fix/stream-expired | #57 | 2d000a26 |
| Test that consumer-group rebalances under load lose and duplicate nothing | files changed (by path): docs 2, tests 1 | test/rebalance-durability | #58 | 2d000a26 |
| Enforce the approval window and requester where the refund is applied | files changed (by path): code 1, other 1, docs 2, tests 1 | fix/approval-enforced-in-worker | #59 | 2d000a26 |
| Test another user against live thread data and define ownership lifetime | files changed (by path): other 1, tests 1 | test/cross-user-live-data | #60 | 2d000a26 |
| Recover from a Redis restore by replaying Kafka; restore ownership in the worker | files changed (by path): code 3, other 2, docs 2, tests 5 | fix/restore-recovery | #61 | 2d000a26 |
| Prove that unreachable tracing backends never fail a request | files changed (by path): other 1, docs 1, tests 2 | test/observability-failure | #62 | 2d000a26 |
| Add a DLQ list and replay tool that actually reprocesses failed messages | files changed (by path): code 1, other 2, docs 1, tests 1 | feat/dlq-replay | #63 | 2d000a26 |
| Rerun latency against pre-registered targets; record durability evidence for 801138e | files changed (by path): code 1, docs 6, tests 1 | perf/latency-rerun | #64 | 2d000a26 |
| Fix the gateway's 2 s produce floor and 503s under load; correct latency percentiles | files changed (by path): code 1, docs 7, tests 2 | perf/producer-latency | #65 | 2d000a26 |
| Fail closed instead of flushing half an entity at the guard's hard limit | files changed (by path): code 1, docs 2, tests 1 | fix/guard-hard-max-leak | #66 | 2d000a26 |
| Keep raw bytes of malformed records out of the DLQ and logs; scan the stack for PII | files changed (by path): code 1, other 3, docs 1, tests 3 | test/sensitive-data-scan | #67 | 2d000a26 |
| Broad PII evaluation with precision, pre-registered targets and intervals | files changed (by path): docs 4, tests 3 | eval/pii-broad | #68 | 2d000a26 |
| Pre-register a final end-to-end safety evaluation before running it | files changed (by path): other 1, docs 1, tests 1 | eval/safety-final | #69 | unrecorded |
| Final end-to-end safety evaluation with the real model, plus a detector-off ablation | files changed (by path): docs 4, tests 1 | eval/safety-final | #70 | 2d000a26 |
| Record the HTTPS path, published ports and credential handling checks | files changed (by path): docs 2 | docs/security-path | #71 | 2d000a26 |
| Answer quality with the real model, scored against a pre-registered rubric | files changed (by path): other 1, docs 5, tests 1 | eval/answer-quality | #72 | 2d000a26 |
| Answer document questions via the RAG service (tenancy, routing, citations) | files changed (by path): code 15, other 14, docs 7, tests 11 | feat/rag-tenancy | #73 | unrecorded |
| ADR 0011 round 1: cross-service auth, audit and contract with the RAG service | files changed (by path): docs 1 | docs/adr-0011-cross-service | #74 | 9c652407 |
| ADR 0011 round 2: accept cross-service auth, audit and contract decisions | files changed (by path): docs 1 | docs/adr-0011-round2 | #75 | 9c652407 |
| Fix LLD diagram, check docs and diagrams in CI, complete the orchestration log | files changed (by path): docs 4, other 4, tests 1 | docs/chatbot-docs-consistency | #76 | 9c652407 |
| Generate summary figures from the repo and produce CI coverage | files changed (by path): docs 3, other 3, tests 1 | docs/figures-and-summary | #77 | 9c652407 |
| Authenticate to the RAG service with per-tenant OAuth client credentials | files changed (by path): code 4, docs 4, other 3, tests 1 | feat/rag-client-credentials | #78 | 9c652407 |
| Export the Redis audit stream to an append-only Postgres table | files changed (by path): code 5, docs 4, other 8, tests 2 | feat/audit-exporter | #80 | 9c652407 |
| Check the dedupe lease before every checkpoint write (stale worker rolled back history) | files changed (by path): code 3, docs 5, tests 1 | fix/fence-history-turns | #84 | b045e33f |
| Log PR #84 in the orchestration record | files changed (by path): docs 1 | docs/log-pr-84 | #85 | b045e33f |
| Run the worker's embedder on one ONNX Runtime thread | files changed (by path): code 1, docs 3, other 2 | perf/embedder-threads | #86 | b045e33f |
| Run scrypt once per accepted credential pair per gateway process | files changed (by path): code 2, docs 6, other 3, tests 1 | perf/gateway-auth-cache | #87 | b045e33f |
| Stop durability tests from rerunning the seed under live workers | files changed (by path): docs 1, tests 1 | fix/durability-no-reseed | #88 | b045e33f |
| Read a thread's state from a head checkpoint only a lease holder can move | files changed (by path): code 2, docs 5, other 1, tests 2 | fix/checkpoint-head | #89 | b045e33f |
| Log PRs #85-#89 in the orchestration record | files changed (by path): docs 1 | docs/orchestration-86-89 | #90 | b045e33f |
| Recheck the stream before reporting a finished reply as expired | files changed (by path): code 1, docs 2, other 1, tests 1 | fix/stream-expired-race | #91 | b045e33f |
| Run several records per worker, one per thread, committing below the oldest in flight | files changed (by path): code 2, docs 7, other 10, tests 2 | perf/worker-concurrency | #92 | b045e33f |
| Log PRs #90-#92 in the orchestration record | files changed (by path): docs 1 | docs/orchestration-91-92 | #93 | b045e33f |
| Expose Prometheus metrics from the gateway and worker and add alert rules | files changed (by path): code 6, docs 6, other 10, tests 1 | feat/prometheus-metrics | #94 | b045e33f |
| Upgrade packages with fixable HIGH and CRITICAL vulnerabilities | files changed (by path): docs 1, other 2 | fix/trivy-findings | #95 | b045e33f |
| Checkpoint a replayed reply before marking it done | files changed (by path): code 1, docs 3, other 1, tests 1 | fix/replay-pending-writes | #97 | b045e33f |
| Move to Python 3.13.18 and clear the remaining image findings | files changed (by path): docs 3, other 9 | chore/python-3.13.18 | #98 | b045e33f |
| Scan service images with Trivy and code with CodeQL in CI | files changed (by path): docs 3, other 1 | ci/security-scans | #99 | b045e33f |



## Orchestration narrative

Claude Code, model `claude-opus-5-5`. The build ran 2026-10-17 10:20–20:17 CDT in session
`25d36d33-a0a9-4fd7-aea8-768129e95968`; PRs #19 and #20 came from a second session whose ID was never
recorded. Later sessions: session 9d3720f9 (audit remediation, #36–#51), 2d000a26 (#52–#72) and 9c652407
(#74, #75, and this reconciliation). The `session` column is the transcript prefix whose `gh pr create`
call produced the pull request, checked against the local Claude Code transcripts; `unrecorded` means
no creating call was found or the ID was never recorded, and none is inferred. The lead (Jayakumar Devaraj) made every gate decision; the agent proposed, implemented,
verified and recorded. Commits carry no AI trailers by rule (CLAUDE.md §5); authorship evidence is
this file and `docs/design_dialogue.md`.

**Design (10:20–10:58).** Preflight found no git repo; the lead supplied the remote and confirmed
the author identity, then the prompt was committed verbatim (`13f2d01`). Step A proposed three
stack bundles with versions resolved live from PyPI, npm, Docker Hub and the Ollama registry
(`docs/design_dialogue.md` Round 1). The lead locked Bundle 1 (`cc71797`), then reopened it with
multi-layer guardrails, LangFuse, Basic auth and a human approval gate (Round 2, `cb1f6c1`),
DeepEval (`254b87e`), and library changes incl. Python 3.13.13 and `en_core_web_lg` (Round 3,
`2334e9a`); final lock in Round 4 (`7ee8466`). Step B produced the roadmap and fit check; the plan
did not fit, so non-protected tasks were cut and the deadline extended (row 0, PR #1, `e321a28`).
At the lead's request the agent added HLD/LLD and ADRs 0001/0005–0007 (row 0b, PR #2, `b6ea716`),
then React and Qdrant-gRPC design updates (row 0c, PR #4, `649c97d`).

**Development (10:58–15:05).** One branch and PR per task, each merged only after its tests ran:
workspace + CI + SPEC (row 1, PR #3); schemas/settings/audit with a real-Redis test (row 2, PR #5);
Presidio scrubber + A1b recall report (row 3, PR #6); compose stack and the two pre-declared spikes,
where (a) passed and (b) triggered the header fallback recorded in ADR 0003 (row 4, PR #7); seed,
leave-one-out thresholds and A2, including a disclosed calibration fix (ADR 0004, row 5, PR #8);
gateway + A1 (row 6, PR #9); worker consumer + A4/A5 (row 7, PR #10); LangGraph pipeline + A6/A7
(row 8, PR #11); SSE + A3 (row 9, PR #12); approval gate (row 10, PR #13); OTel → Jaeger + LangFuse
with a functional report (row 11, PR #14, `docs/reports/e2e-observability.md`); React chat window +
A8 (row 14, PR #16). A lead-reported CPU issue was traced to the Kafka healthcheck spawning a JVM
every 5 s and fixed (PR #15, `f11fdfa`).

**QA (throughout, then 15:05–17:40).** Unit tests used labeled test doubles; persistence,
messaging, routing and approval paths were verified against real Kafka, Redis, Qdrant and Jaeger
(rung 2), and process-level smoke runs covered `worker.main` and the gateway (rung 3). Failures were
fixed and disclosed in PR bodies rather than hidden: the A2 calibration mismatch (PR #8), A4/A5
harness timing and an IPv6 `localhost` stall (PR #10, two extra cycles approved by the lead), the
mock delta spacing caught by A6 (PR #11), and the A8 stream-double timing (PR #16). Phase 2.5 added
integration and frontend jobs to CI (row H1, PR #17, run 39065303808: all three jobs green) and
measured 87 % coverage (`docs/reports/coverage.txt`). A ~1h50m CI wait ended Phase 2.5 early; wrap-up
reran every documented command before merge (PR #18).

### Addendum — post-wrap-up extension (17:07–19:25 CDT)

The lead asked to build the cut items and extended the deadline to 19:30, then 20:30. Task 12 images
were pushed as unmerged WIP when Docker Desktop crashed (`05b749d`); another session capped compose
resources (PR #19) and merged the WIP branch (PR #20). This session then verified it end to end in
containers (PR #21, `docs/reports/e2e-containers.md`), added A10 (PR #22), A9 with a real Ollama
round trip — which exposed and fixed an SSE relay bug where the gateway's Redis client timed out its
own `XREAD BLOCK` (PR #23, regression test `tests/integration/test_stream_slow_first_event.py`) —
Caddy TLS/HTTP/2 (PR #24), the first A11 latency measurement (PR #25), and this reconciliation.

PR numbers for rows above that were written as "see PR" (appended, not edited, to keep this log
append-only): 12 verification → #21 · 15 A10 scale → #22 · 13 A9 real Ollama → #23 · T3 Caddy TLS →
#24 · T3 A11 latency → #25 · reconcile + docs → #26 · review findings → #27 · echo fix → #28.

Addendum (19:22–20:30): review follow-up per the lead — latency investigated (worker CPU bottleneck,
AIOProducer 1 s buffer; one fix attempt broke delivery and was reverted before commit, PR #27), the
1B-model echo filtered in the output guard (PR #28); A4 live-Qdrant-stop automation, Caddy-only port
exposure, and the injection false-positive decision remain open (`docs/known_limitations.md`).

Addendum (after the 20:30 stop, lead-requested): corrections to the narrative header above. The
build ran 10:20–20:17 CDT in Claude Code session `25d36d33-a0a9-4fd7-aea8-768129e95968`
(model `claude-opus-5-5`); PRs #19 and #20 came from a second session whose ID was not recorded, so
"single agent throughout, one session, 10:20–17:40" is superseded. The 87 % coverage figure in the
QA paragraph was measured at PR #18; the current report is 88 % at `c3b4f0f` (PR #29). Remove open
risk → #30. Work after 20:30 is outside the challenge snapshot `399e970`.

Addendum (audit remediation, from 2026-10-17 22:00 CDT): fixes for
`docs/reports/project-audit-2026-10-17.md` in Claude Code session
`9d3720f9-c703-48da-98b6-8e84e2261304` (model `claude-opus-5-5`), worked in a separate git worktree
after a first attempt shared the main checkout with the concurrent demo-script session and one of
that session's commits landed on this session's branch (discarded unpushed; the demo work was
re-committed as PR #35). One PR per finding, each with a test that failed before the fix:
ownership lifetime → #36 · acceptance protocol + client retry identity → #37 · refund atomicity → #38 · replay idempotency + pending-approval supersede → #39 · durable Redis/Kafka state (ADR 0008) → #40 · scrypt off the event loop → #41 · liveness vs readiness → #42 · refusal evaluation (pre-registered in `ce60129`, candidate rejected) → #43 · contrastive injection detector (pre-registered in `3a97b3f`, adopted by the lead below target) → #44 · DeepEval harness (pre-registered in commit 3ef0d66 on the unmerged branch feat/deepeval-answer-quality, so it is not in main; first run timed out; paused by the lead) · docs reconciliation → #45 · A4 live Qdrant outage test → #46 · A5 live worker-kill test → #47 · A4 test waits for Qdrant → #48 · output-guard chunk leak → #49 · harder PII recall set → #51.

### Addendum — PRs #52–#75 (2026-10-18 to 2026-10-28)

Rows for these PRs are in the table above; titles and file-type counts come from GitHub, not from memory.

- **Audit-driven fixes (#52–#57, #59, #61, #63, #66, #67):** idempotent acceptance retries, refund receipt
  validation, per-attempt lease fencing, a dedupe window longer than Kafka retention, expired-stream
  handling, approval enforced where the refund is applied, Redis-restore recovery, a DLQ list-and-replay
  tool, fail-closed output guard at its hard limit, and keeping raw malformed bytes out of the DLQ and logs.
- **Diagram fix (#54):** commit `9e3c967` removed `;` from sequence-diagram text. The same defect returned
  in the diagram added by #73 and is fixed again in the PR that adds the `docs` CI job.
- **Added tests (#58, #60, #62):** consumer-group rebalances, a second user against live thread data, and
  unreachable tracing backends.
- **Latency (#64, #65):** a re-run against pre-registered targets, then the gateway produce-floor fix.
- **Pre-registered evaluations (#68–#72):** broad PII, final safety (with a detector-off ablation), the
  security path, and answer quality. Each pre-registered its targets before running.
- **RAG integration (#73):** document questions via the separate RAG service (ADR 0010).
- **Cross-service decisions (#74, #75):** ADR 0011, round 1 then round 2.
- **Enterprise audit follow-ups (#84–#92):** history fencing and the head pointer that closes its race (#84, #89), the durability test seed fix (#88), and two latency fixes measured before and after (#86, #87), a gateway race that reported finished replies as expired (#91), and several records in flight per worker (#92, ADR 0012). Ops and security: Prometheus metrics and alert rules (#94, ADR 0013), a replayed reply dropped from history after a crash between its writes and its checkpoint, found with per-thread diagnostics (#97), image findings cleared (#95, #98) and Trivy and CodeQL in CI (#99, ADR 0014).
- **Not recorded:** the creating session of #69 and #73, and of #17, #19, #20 (see the table).
