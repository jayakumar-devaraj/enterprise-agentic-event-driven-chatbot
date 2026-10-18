# AI orchestration

Build-process record (not the runtime audit trail). Agent: Claude Code, model `claude-opus-5-5`,
single session. Lead: Jayakumar Devaraj (gates and decisions).

| task | generated (code/tests/docs) | branch | PR # |
|---|---|---|---|
| 0 persist plan | docs: master_plan, specs 1–11, design_dialogue, known_limitations, this log | plan/setup | #1 |
| 0b design docs | docs: HLD, LLD, ADR 0001/0005/0006/0007, master_plan update | task-0b-design-docs | #2 |
| 1 workspace skeleton | code: uv workspace, CI; tests: workspace import smoke; docs: SPEC, README, .env.example, CLAUDE.md §9 | task-1-workspace-skeleton | #3 |
| 0c design update | docs: React chat window + Qdrant gRPC in HLD/LLD, ADR 0006 rows; config keys | task-0c-design-react-grpc | #4 |
| 2 common schemas/settings | code: schemas, settings, logjson, audit; tests: 25 unit + 1 integration (real Redis) | task-2-common-schemas-settings | #5 |
| 3 PII scrubber | code: scrubber; tests: 9 scrubber/harness unit; eval: A1b recall report; docs: limitations | task-3-pii-scrubber | #6 |
| 4 compose infra + spikes | code: deploy/compose.yaml, ollama Dockerfile; tests: 3 spike/audit integration (real Kafka, Redis); docs: ADR 0002, 0003 | task-4-compose-infra-spikes | #7 |
| 5 seed + routing | code: seed main/calibrate, worker embedding/router; data: seed/eval/injection; tests: 5 unit + A2 integration (real Qdrant gRPC); docs: ADR 0004 | task-5-seed-routing | #8 |
| 6 gateway POST /chat | code: gateway app/auth/producer; tests: 12 unit (doubles) + A1 integration (real Kafka, Redis) | task-6-gateway-chat | #9 |
| 7 worker consumer | code: worker consumer/streams, compose IPv4 listener; tests: 5 unit (doubles) + A4, A5×2 integration (real Kafka, Redis) | task-7-worker-consumer | #10 |
| 8 LangGraph pipeline | code: worker graph/llm/guards/main; tests: 5 unit + A6, A7×2 integration (real Redis checkpointer); process smoke | task-8-langgraph-graph | #11 |
| 9 token stream + SSE | code: gateway sse relay + stream endpoint; tests: A3 + owner/422 integration (real Redis, real graph) | task-9-token-stream-sse | #12 |
| 10 approval gate | code: worker approval/graph interrupt, gateway /approve; tests: 1 unit + 3 integration (real Redis) | task-10-approval-gate | #13 |
| 11 observability | code: chat_common.tracing, gateway instrumentation, worker spans + LangFuse observer; tests: Jaeger integration; docs: e2e report (rung 3), ADR 0003 note | task-11-observability | #14 |
| fix kafka CPU | config: Kafka TCP healthcheck, kafka-init readiness wait; verified CPU samples + 18 integration tests | fix-kafka-healthcheck-cpu | #15 |
| 14 React chat window | code: frontend app + Dockerfile, compose service; tests: A8 (5 Vitest) | task-14-react-chat-window | #16 |
| H1 CI integration + frontend | config: CI jobs integration (compose) and frontend; verified run 39065303808 | hardening/ci-integration | #17 |
| wrapup | docs: README sections, demo script, summary, coverage report, limitations, this narrative | wrapup | #18 |

## Orchestration narrative

Single agent throughout: Claude Code, model `claude-opus-5-5`, one session, 2026-10-17 10:20–17:40
CDT. The lead (Jayakumar Devaraj) made every gate decision; the agent proposed, implemented,
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
| 12 service images (WIP push) | code: gateway/worker/seed Dockerfiles, compose services; merged as PR #20 by another session before e2e verification | task-12-service-images | #20 |
| compose resource caps | config: memory/CPU caps, Kafka heap; authored and merged in another session after Docker Desktop crashed (not this session's work) | (other session) | #19 |
| 12 verification | docs: `docs/reports/e2e-containers.md` (containerized e2e verified after the fact) | task-12-verification | see PR |
| 15 A10 scale | tests: tests/load/test_scale_a10.py (marker `scale`); docs: docs/reports/a10-scale.md | task-15-scale-a10 | see PR |
| 13 A9 real Ollama | code: gateway Redis socket timeout fix + regression test, Ollama Dockerfile PID fix, worker MOCK_LLM env; tests: tests/load/test_ollama_a9.py (marker `llm`); docs: a9 report | task-13-ollama-a9 | see PR |
