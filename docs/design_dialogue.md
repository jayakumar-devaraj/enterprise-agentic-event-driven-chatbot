# Design dialogue — Step A

Live transcript of Phase 1 Step A (stack bundles + architecture). Agent: Claude Code, model
`claude-opus-5-5`. Append-only after lock.

## Round 1 — agent proposal (2026-10-17 10:25 CDT)

Versions below were resolved live at 10:23–10:25 CDT from PyPI JSON, the npm registry, Docker Hub,
the Ollama registry, nodejs.org and GitHub releases (spaCy models). None are from memory.

### Held fixed in all three bundles (mandated by `docs/prompts/problem-statement.md` "Stack (fixed)")

Language Python 3.14.5 via uv 0.11.19 (cp314 Linux wheels confirmed for onnxruntime 1.31.0,
confluent-kafka 2.16.0, thinc 8.3.13, numpy 2.5.4, blis 1.3.3) · FastAPI 0.143.3 + uvicorn[standard]
0.54.0 · confluent-kafka 2.16.0 on `apache/kafka:4.3.1` (KRaft) · Redis `redis:8.10.2` + redis-py
8.1.0 (token streams, dedupe, rate limit) · Qdrant `qdrant/qdrant:v1.19.2` + qdrant-client 1.19.1 ·
fastembed 0.9.0 with `BAAI/bge-small-en-v1.5` · presidio-analyzer/anonymizer 2.2.364 + spaCy 3.8.16 ·
langgraph 1.2.16 · openai 3.33.0 (OpenAI-compatible client) → `ollama/ollama:0.40.6` · pydantic 2.14.1
+ pydantic-settings 2.16.0 · OpenTelemetry SDK/OTLP 1.45.1 + instrumentation-fastapi 0.66b1 →
Jaeger v2 2.22.0 · Frontend: Node 24.22.0 (active LTS "Krypton"), React 19.3.1, TypeScript 7.0.2,
Vite 8.3.4, Vitest 5.0.5, @testing-library/react 16.3.3, jsdom 30.1.3 · Caddy 2.11.7 (Tier 3) ·
pytest 9.1.1, pytest-asyncio 1.4.0, pytest-cov 7.1.0, ruff 0.17.1.

Language does not vary: the brief fixes Python + TypeScript, so a language swap is not a genuine
trade-off here. Bundles vary on the axes the brief leaves open: checkpointer, spaCy model size,
Ollama model tag, eval tooling, logging.

### Bundle 1 — Lean, spec-default · **Recommended**

- **Stack:** fixed set + langgraph-checkpoint-redis 0.5.2 (Redis does checkpoints too), spaCy
  `en_core_web_sm` 3.8.0 (12 MB wheel), Ollama `qwen2.5:0.5b` (379 MB), eval = pytest harness +
  plain-Python metric scripts (LOO threshold, recall, p95), structlog 26.1.0.
- **Protected concerns:** Guardrails — Presidio at ingress and on model output, Pydantic validation,
  413 size cap, user text only in a delimited user-message field. Observability — OTel traces to
  Jaeger + structlog JSON carrying `trace_id`. Access control — not built: auth is a brief non-goal;
  `thread_id` is unauthenticated and is logged as a limitation, stateless gateway keeps auth
  addable. Human approval gate — N/A: the agent only writes text to the caller's own stream; no
  side-effecting actions.
- **Trade-off:** fastest build, fewest containers; lowest PII recall on PERSON/LOCATION.
- **Failure mode:** spike (a) fails → switch to the Postgres fallback mid-Tier 0.
- **API confidence:** unsure of `fastapi.sse.EventSourceResponse`, confluent-kafka asyncio classes,
  `AsyncRedisSaver`, openai 3.x, TS 7 + Vite 8.
- **Version policy:** latest stable; Node on LTS.

### Bundle 2 — Accuracy-first

- **Stack:** fixed set + langgraph-checkpoint-redis 0.5.2, spaCy `en_core_web_lg` 3.8.0 (382 MB
  wheel), Ollama `llama3.2:1b` (1259 MB), eval = pytest + DeepEval 4.3.1 (LLM-judged answer quality
  with the local Ollama as judge), structlog 26.1.0.
- **Protected concerns:** as Bundle 1, plus a DeepEval answer-relevance check on the
  `MOCK_LLM=false` path.
- **Trade-off:** best PII recall and answer quality; slowest builds and CPU inference.
- **Failure mode:** bigger images and a 1.2 GB model slow every rebuild and CI; DeepEval with a
  CPU judge is slow and nondeterministic; DeepEval is outside the brief's stack (needs approval).
- **API confidence:** as Bundle 1, plus DeepEval's custom-model judge API.
- **Version policy:** latest stable; Node on LTS.

### Bundle 3 — Conservative, fallback-first

- **Stack:** fixed set + langgraph-checkpoint-postgres 3.2.0 + psycopg 3.3.6 on `postgres:18.6`
  (spike (a) skipped), spaCy `en_core_web_md` 3.8.0 (31 MB), Ollama `qwen3:0.6b` (498 MB), eval =
  pytest + scikit-learn 1.9.1 (classification report, LOO), stdlib `logging` with a JSON formatter.
- **Protected concerns:** as Bundle 1; checkpoints live in Postgres, so PII-scrubbed-before-checkpoint
  is enforced at a different boundary.
- **Trade-off:** most mature checkpointer; one more stateful container and two stores to operate.
- **Failure mode:** extra service in compose and in CI integration jobs; Postgres is not in the
  brief's primary stack (it is the declared fallback, so using it up front is a deviation).
- **API confidence:** as Bundle 1 minus `AsyncRedisSaver`, plus `AsyncPostgresSaver` setup.
- **Version policy:** latest stable; Postgres 18 major line; Node on LTS.

### Shared architecture

```
 Browser (React/Vite :5173)
   │ POST /chat                         ▲ SSE GET /stream/{thread}/{msg}
   ▼                                    │ (Last-Event-ID resume)
 ┌───────────── Gateway (FastAPI, stateless, N replicas) ─────────────┐
 │ validate (Pydantic) → 413/422 → Redis rate limit (429)             │
 │ Presidio scrub (to_thread) → envelope{msg,thread,text,ts,trace_id} │
 └──────┬──────────────────────────────────────────────▲──────────────┘
        │ produce key=thread_id (503 if Kafka down)    │ XREAD tokens:{thread}:{msg}
        ▼                                              │
  Kafka 4.x KRaft: chat-requests (P partitions) ─► chat-requests.dlq
        │ consumer group (replicas ≤ P), manual commit
        ▼
 ┌──────────────────── Worker (LangGraph StateGraph) ─────────────────┐
 │ dedupe SET NX lease → node route: fastembed(to_thread) → Qdrant    │
 │ top-1 (score<ROUTE_THRESHOLD ⇒ general) → billing|tech_support|    │
 │ general → OpenAI-compatible client → Ollama :11434 | mock          │
 │ scrub output → checkpoint per thread_id → XADD token/done/error    │
 └─────┬───────────────┬──────────────────────┬───────────────────────┘
       ▼               ▼                      ▼
  Qdrant `intents`  Checkpointer          Redis streams + dedupe
  (seeded by        Redis (B1,B2) |
   one-shot `seed`) Postgres (B3)
 OTel spans gateway → (envelope trace_id / Kafka header) → worker → Jaeger v2 :16686
```

Shape changes: Bundle 3 adds a Postgres box on the checkpoint arrow; Redis remains for streams.
Bundle 2 adds an offline DeepEval step on the evaluation side only.

### E3 — per-library confirmation

1. spaCy `sm` (12 MB) vs `md` (31 MB) vs `lg` (382 MB)?
2. Ollama `qwen2.5:0.5b` vs `qwen3:0.6b` vs `llama3.2:1b`?
3. Checkpointer: Redis (spike first) vs Postgres up front?
4. Eval: plain pytest metric scripts vs scikit-learn vs DeepEval?
5. Logging: structlog vs stdlib JSON formatter?
6. Python 3.14.5 (latest) vs 3.13.13 (wider wheel coverage)?
7. TypeScript 7.0.2 (latest) vs staying on the 5.x line if Vite/Vitest compatibility bites?

## Round 1 — lead reply (verbatim)

> bundle 1

## Lock (2026-10-17, agent confirmation)

Reply names exactly one bundle and changes nothing, so per Gate A it locks as-is: **Bundle 1 —
Lean, spec-default**. E3 answers default to the Bundle 1 column: spaCy `en_core_web_sm`, Ollama
`qwen2.5:0.5b`, Redis checkpointer (spike first, Postgres fallback), pytest + plain metric scripts,
structlog, Python 3.14.5, TypeScript 7.0.2.

## Round 2 — lead replies after lock (verbatim, received 10:36–10:38 CDT)

> Guardrails - multi layer

> Observability - LangFuse

> Access control - basic auth

> Human approval gate - Yes

## Round 2 — agent response (10:39 CDT)

These change the locked bundle's protected concerns, so per Gate A the result is echoed back and
the stack is not re-locked until an explicit "yes"/"lock". Versions resolved live: langfuse (PyPI)
5.0.2; `langfuse/langfuse` and `langfuse/langfuse-worker` images 4.63.0; `clickhouse/clickhouse-server`
26.9.20. llm-guard 0.3.16 rejected: requires Python <3.13 and pulls PyTorch, violating the brief's
no-PyTorch rule and the locked Python 3.14.5.

- **Guardrails (multi-layer), no new dependency:** L1 schema/size (Pydantic strict, 413, 422,
  `thread_id` regex) · L2 abuse (Redis per-thread rate limit, 429) · L3 PII (Presidio scrub before
  Kafka) · L4 injection: structural (byte-identical system prompt, user text only in a delimited
  user field) + detection (fastembed similarity against injection exemplars in a separate Qdrant
  collection; a hit routes to a refusal node, flagged on the span and in logs, never silently dropped)
  · L5 output (Presidio scrub of model output, token cap, system-prompt-echo check before XADD).
- **Observability = LangFuse:** options (A) self-hosted LangFuse alongside OTel→Jaeger, (B) LangFuse
  Cloud alongside Jaeger, (C) LangFuse replacing Jaeger. Recommended A. Awaiting choice.
- **Access control = HTTP Basic:** gateway checks credentials from `AUTH_USERS` (stdlib scrypt hashes);
  ownership binding `thread_owner:{thread_id}` SET NX on first POST, 403 on mismatch for POST and
  stream; envelope carries `user_id`; 401 on missing/bad credentials. Frontend reads SSE via `fetch`
  because `EventSource` cannot send an Authorization header.
- **Human approval gate:** the brief's agent has no side-effecting action to gate. Options: (A) add a
  billing "refund request" action gated by LangGraph `interrupt()` + `POST /approve`, (B) gate an
  operator DLQ re-drive command. Recommended A. Awaiting choice.

> Eval : DeepEval

Agent: DeepEval 4.3.1 swaps in from Bundle 2; same language, so the bundle stays consistent. Core
deps checked live: no PyTorch; it ships posthog telemetry, so it is dev-only (never in a runtime
image) with telemetry opted out. Judge = the local Ollama model through the OpenAI-compatible URL;
deterministic metrics (routing accuracy, PII recall, LOO threshold) stay plain pytest.

## Round 3 — lead reply (verbatim)

> E3 — per-library choices (answer any you want to change): 1. lg 2. llama3.2:1b 3. redis 4. deepeval 5. stdlib json 6. 3.13.13 7 which one you would recommend ?

## Round 3 — agent response (10:40 CDT)

Item 7 checked live: typescript latest 7.0.2, 6.x line 6.0.3, 5.x line 5.9.3. typescript-eslint 8.71.2
declares peer `typescript >=4.8.4 <6.1.0`, so TS 7 would block type-aware linting; vite 8.3.4,
@vitejs/plugin-react 6.1.2 and vitest 5.0.5 declare no TypeScript peer. Recommendation: **TypeScript
6.0.3** (newest line the lint toolchain supports). Python 3.13.13: tokenizers ships a cp310-abi3
manylinux wheel, so the earlier cp313 gap is not a blocker.

Echoed bundle (not locked until "yes"): Python 3.13.13 · spaCy `en_core_web_lg` 3.8.0 · Ollama
`llama3.2:1b` · Redis checkpointer (langgraph-checkpoint-redis 0.5.2) · DeepEval 4.3.1 (dev-only) ·
stdlib `logging` + JSON formatter (structlog dropped) · TypeScript 6.0.3 · multi-layer guardrails ·
HTTP Basic + thread ownership · LangFuse option and approval-gate option still awaiting choice.

## Round 4 — lead reply (verbatim)

> B, A, yes

## Lock (final, 10:43 CDT)

Locked: Python 3.13.13 (uv) · FastAPI 0.143.3 + uvicorn 0.54.0 · confluent-kafka 2.16.0 / Kafka
4.3.1 · Redis 8.10.2 + langgraph-checkpoint-redis 0.5.2 · Qdrant v1.19.2 · fastembed 0.9.0 ·
Presidio 2.2.364 + spaCy `en_core_web_lg` 3.8.0 · langgraph 1.2.16 · openai 3.33.0 → Ollama 0.40.6
`llama3.2:1b` · OTel 1.45.1 → Jaeger 2.22.0 **and LangFuse Cloud** (langfuse 5.0.2; keys supplied by
the lead via `.env`, scrubbed content only leaves the machine) · stdlib JSON logging · DeepEval 4.3.1
dev-only · TypeScript 6.0.3 frontend · guardrails L1–L5 · HTTP Basic + thread ownership · human
approval gate = billing "refund request" paused by LangGraph `interrupt()` until `POST /approve`.
