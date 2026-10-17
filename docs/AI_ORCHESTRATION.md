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
