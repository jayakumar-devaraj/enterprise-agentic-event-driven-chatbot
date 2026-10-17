# Master plan

Single source of truth for the build. Any agreed cut updates this file in the same commit.
Process: `docs/prompts/coding_challenge.md`. Requirements: `docs/prompts/problem-statement.md`.
Design transcript: `docs/design_dialogue.md`.

## Window

- Start 10:20 CDT 2026-10-17. Deadline extended by the lead (option R1) from 15:20 to **16:40 CDT**
  (6 h 20 min).
- Reserve = max(30 min, 15 % × 380 min) = 57 min. Phase 3 floor = max(20 min, 10 % × 380 min) = 38 min.

## Architecture

```
 Browser / curl  ── HTTP Basic ──┐
   │ POST /chat                  │        ▲ SSE GET /stream/{thread}/{msg}  (Last-Event-ID resume)
   ▼                             ▼        │ POST /approve/{thread}/{msg}
 ┌────────────── Gateway (FastAPI, stateless, N replicas) ──────────────┐
 │ L1 auth 401 · owner check 403 · Pydantic 422 · size 413               │
 │ L2 Redis per-thread rate limit 429 · idempotent 202 on repeat msg_id  │
 │ L3 Presidio scrub (to_thread) → envelope{msg,thread,user,text,ts,tid} │
 └──────┬──────────────────────────────────────────────▲────────────────┘
        │ produce key=thread_id (503 if Kafka down)    │ XREAD tokens:{thread}:{msg}
        ▼                                              │
  Kafka 4.3 KRaft: chat-requests (KAFKA_PARTITIONS) ─► chat-requests.dlq
        │ consumer group (replicas ≤ partitions), manual commit after durable outcome
        ▼
 ┌──────────────────── Worker (LangGraph StateGraph) ───────────────────┐
 │ dedupe SET NX lease → route node: fastembed (to_thread) → Qdrant     │
 │   L4 injection collection hit ⇒ refuse node                          │
 │   intents top-1 < ROUTE_THRESHOLD ⇒ general | billing | tech_support │
 │ billing refund action ⇒ interrupt() ⇒ ApprovalRequired event; resumed│
 │   by POST /approve (approval message on Kafka) ⇒ apply + audit       │
 │ OpenAI-compatible client → Ollama llama3.2:1b | mock (MOCK_LLM=true) │
 │ L5 output scrub + token cap + system-prompt echo check               │
 │ checkpoint per thread_id (Redis) → XADD token/done/error events      │
 └─────┬───────────────┬────────────────────┬──────────────┬────────────┘
       ▼               ▼                    ▼              ▼
  Qdrant intents   Redis checkpoints   Redis streams    Redis stream audit:events
  + injection_     (langgraph-         dedupe, rate     (durable, queryable runtime
  patterns (seed)  checkpoint-redis)   limit, owners    audit trail)
 Traces: OTel SDK gateway → worker (envelope trace_id / Kafka header) → Jaeger v2 :16686
         LangFuse Cloud (US) for LLM-level spans — scrubbed content only
```

Failure and scaling: infra faults (Qdrant / LLM / Redis) get bounded retries with backoff, then an
`ErrorEvent{dependency_unavailable}` on the stream plus a DLQ record, then commit. Malformed envelopes
go to the DLQ without retry. The gateway is stateless, so any replica serves any stream. Worker
replicas are bounded by the partition count; per-thread order comes from the Kafka key.

## Locked stack

See the final lock in `docs/design_dialogue.md` (Round 4). Python 3.13.13 via uv; FastAPI 0.143.3;
uvicorn 0.54.0; confluent-kafka 2.16.0 / `apache/kafka:4.3.1`; `redis:8.10.2` + redis-py 8.1.0 +
langgraph-checkpoint-redis 0.5.2; `qdrant/qdrant:v1.19.2` + qdrant-client 1.19.1; fastembed 0.9.0
(`BAAI/bge-small-en-v1.5`); presidio 2.2.364 + spaCy 3.8.16 `en_core_web_lg` 3.8.0; langgraph 1.2.16;
openai 3.33.0 → `ollama/ollama:0.40.6` `llama3.2:1b`; pydantic 2.14.1; pydantic-settings 2.16.0;
OpenTelemetry 1.45.1 → Jaeger 2.22.0; langfuse 5.0.2; stdlib JSON logging; pytest 9.1.1;
pytest-asyncio 1.4.0; pytest-cov 7.1.0; ruff 0.17.1; DeepEval 4.3.1 (dev-only). Frontend (cut):
Node 24.22.0, React 19.3.1, TypeScript 6.0.3, Vite 8.3.4, Vitest 5.0.5.

## Layout (binding)

```
packages/common/chat_common/   schemas, settings, scrubber, logjson, audit   (__init__.py)
services/gateway/gateway/      app, auth, ratelimit, producer, sse    + Dockerfile
services/worker/worker/        consumer, graph, router, llm, guards, approval + Dockerfile
services/seed/seed/            main, calibrate                        + Dockerfile
deploy/compose.yaml
data/intents.seed.jsonl  data/intents.eval.jsonl  data/injection.seed.jsonl
tests/unit/  tests/integration/ (@pytest.mark.integration)  tests/fixtures/  tests/load/
frontend/  docs/{adr,specs,prompts}/  SPEC.md  pyproject.toml (uv workspace; dev group never in images)
```

## Roadmap

| # | Task | Est (min) | Protects |
|---|---|---|---|
| 0 | Persist plan | 5 | |
| 1 | Workspace skeleton, README Tech Stack, `.gitattributes`, SPEC.md, `.env.example`, CI (lint+unit), CLAUDE.md §9 fill | 20 | |
| 2 | common: schemas, settings (lease formula), JSON logging, audit-stream writer | 20 | audit trail |
| 3 | common: Presidio `lg` scrubber (off-loop) + A1b recall/p95 report | 25 | Guardrails L3 |
| 4 | Compose infra + spikes (a) Redis checkpointer, (b) confluent-kafka asyncio headers + ADRs 0001–0003 | 30 | |
| 5 | Seed service + data + LOO thresholds ADR + A2 | 30 | Guardrails L4 |
| 6 | Gateway `POST /chat`: Basic auth + ownership, L1/L2/L3, idempotent 202, produce/503 + A1 | 30 | Access control |
| 7 | Worker: consumer, dedupe lease, manual commit, retries, DLQ + A4 (malformed), A5 | 30 | |
| 8 | LangGraph: route, refusal, intent nodes, mock LLM, Redis checkpointer, output guard + A6, A7 | 30 | Guardrails L4/L5 |
| 9 | Redis token stream + SSE (Last-Event-ID, owner check) + A3 | 25 | |
| 10 | Human approval gate: refund action, `interrupt()`, `POST /approve`, audit + tests | 35 | Approval gate |
| 11 | OTel → Jaeger + LangFuse Cloud spans | 30 | Observability |

Σ = 310 min.

### Cut at Step B (recorded in `docs/known_limitations.md`)

Cut in this order because they are not protected concerns and the plan did not fit: Tier 3 (Caddy
TLS + A11), Task 15 (A10 scale test), Task 14 (React client + A8), Task 13 (DeepEval + A9 real
Ollama), Task 12 (service images + full-compose e2e + A4 Qdrant-stop). Candidates to restore from
reserve under Phase 2.5 item 5.

## Criteria coverage

| Criterion | Evidence task |
|---|---|
| A1 PII in Kafka record | 6 |
| A1b PII recall + p95 | 3 |
| A2 routing | 5 |
| A3 streaming | 9 |
| A4 failure | 7 (malformed); Qdrant-stop part cut with 12 |
| A5 idempotency | 7 |
| A6 memory | 8 |
| A7 injection | 8 |
| A8 frontend · A9 Ollama · A10 scale · A11 latency | cut (14, 13, 15, Tier 3) |
| Tier 0 spikes, ADRs | 4 (0001–0003), 5 (threshold) |
| Tier 2 OTel → Jaeger | 11 |
| Rubric 1a design doc | 0 (this file) + ADRs |
| Rubric 1b design-agent evidence | `docs/design_dialogue.md` |
| Rubric 2a robustness + durable queryable audit trail | 2, 6, 7, 10 |
| Rubric 2b dev-velocity evidence | `docs/AI_ORCHESTRATION.md` rows + PRs, every task |
| Rubric 3a tests + coverage report | every task; coverage in Phase 2.5 item 2 |
| Rubric 3b AI-executed test evidence | CI from 1; Phase 2.5 item 1–2 |
| Rubric 4a leadership doc | Phase 3 `docs/summary.md` |
| Rubric 4b orchestration narrative | Phase 3 |

## Config defaults (approved with the plan)

| Key | Default |
|---|---|
| KAFKA_BOOTSTRAP_SERVERS | kafka:9092 |
| KAFKA_PARTITIONS | 4 |
| KAFKA_TOPIC / KAFKA_DLQ_TOPIC | chat-requests / chat-requests.dlq |
| KAFKA_CONSUMER_GROUP | chat-workers |
| REDIS_URL | redis://redis:6379/0 |
| QDRANT_URL | http://qdrant:6333 |
| QDRANT_COLLECTION / QDRANT_INJECTION_COLLECTION | intents / injection_patterns |
| EMBED_MODEL | BAAI/bge-small-en-v1.5 |
| SPACY_MODEL | en_core_web_lg |
| ROUTE_THRESHOLD / INJECTION_THRESHOLD | set by leave-one-out in Task 5, recorded in an ADR |
| LLM_BASE_URL | http://ollama:11434/v1 |
| LLM_MODEL / LLM_MAX_TOKENS | llama3.2:1b / 256 |
| MOCK_LLM | true (CI/tests) |
| INFERENCE_TIMEOUT_S / MAX_RETRIES / RETRY_BACKOFF_MAX_S | 60 / 2 / 4 |
| DEDUPE_LEASE_S / DEDUPE_TTL_S | 200 (> 60×3 + 4×2 = 188) / 86400 |
| CHECKPOINT_TTL_MIN | 1440 |
| STREAM_MAXLEN / STREAM_TTL_S / STREAM_BLOCK_MS | 2000 / 3600 / 5000 |
| MAX_MESSAGE_CHARS (413 above) | 4000 |
| RATE_LIMIT_PER_MIN (per thread) | 20 |
| CORS_ORIGINS | http://localhost:5173 |
| AUTH_USERS | labeled placeholder (`user:scrypt-hash`) |
| APPROVAL_TTL_S | 3600 |
| OTEL_EXPORTER_OTLP_ENDPOINT | http://jaeger:4317 |
| LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY | labeled placeholders (real values only in `.env`) |
| LANGFUSE_BASE_URL | https://us.cloud.langfuse.com (VERIFY: name read by langfuse 5.0.2) |
| LOG_LEVEL / HF_HUB_OFFLINE | INFO / 1 |

## Decisions at Gate B

- Layout: the problem statement's (`services/`, `packages/`), not `src/<responsibility>/`.
- CLAUDE.md §9: one-time exception to fill §9 only, in Task 1, own commit; §1–8 untouched.
- Commit trailers: none (CLAUDE.md §5); authorship evidence is `docs/design_dialogue.md` and
  `docs/AI_ORCHESTRATION.md`.
