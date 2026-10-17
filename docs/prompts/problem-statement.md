# Build Brief: enterprise-agentic-event-driven-chatbot (v2.4)

Precedence: explicit instruction in chat > this brief > repo `CLAUDE.md` > model defaults. On conflict: say so in one sentence and ask.

## Role
Principal backend/ML platform engineer. Build one containerized, event-driven chatbot monorepo that scales horizontally. Report only what you observed.

## Problem
Synchronous chatbots time out (504) under load, spend commercial-LLM money on intent classification, and leak PII into orchestration. Deliver a decoupled pipeline: scrub PII at ingress, route intent with local vector math, stream tokens to the client. Runtime cost $0 on CPU. One-time model/image downloads happen at build time only and are listed in the README; containers run with `HF_HUB_OFFLINE=1`.

Non-goals (ask before adding): Kubernetes/Helm, GPU serving, multi-node Kafka/Redis, auth. The design must not block them: stateless gateway, partition-scaled workers, OpenAI-compatible inference URL.

## Time box and tiers
Budget runs from the "go ahead" message; time waiting at a gate is excluded and logged separately. Read `date` at kickoff, at every recap, and at each mark below. If a tier is not verified at its mark, stop and propose a cut before writing more code. Never drop a requirement silently.

| Tier | Mark | Scope |
|---|---|---|
| 0 Bootstrap | 0:45 | `git init`; author identity per `CLAUDE.md` §5 (show, wait for confirmation); confirm a remote (ask if none); `.gitattributes` forcing LF; fill `CLAUDE.md` §9 with no placeholders; `SPEC.md` = "Contracts" below; `docs/AI_ORCHESTRATION.md`; GitHub Actions CI (lint + unit). **Spikes:** (a) the pinned LangGraph Redis checkpointer writes and reads a checkpoint on the chosen Redis image; (b) the pinned `confluent-kafka` asyncio producer/consumer import path and per-message header support. Spike fails ⇒ apply the pre-declared fallback, write the ADR, then start Tier 1. |
| 1 Must | 3:30 | Compose stack, seeder, gateway + Presidio, Kafka + DLQ, worker, embeddings + Qdrant routing, LangGraph + Redis checkpointing, mock LLM, Redis token stream, SSE. Tests A1-A7. |
| 2 | 4:30 | OpenTelemetry traces gateway→worker exported to Jaeger (fixed standard in `CLAUDE.md` §9; cutting it needs my explicit sign-off); horizontal-scale test A10; React client + A8; real Ollama + A9. |
| 3 (cut first) | — | Caddy reverse proxy with TLS/HTTP2 (internal self-signed CA); latency measurement A11; polish. |

## Stack (fixed)
Resolve every version by querying the environment or the package index at build time and pin it. Never state a version from memory; anything unresolved is flagged `VERIFY:`. Use the latest stable release of each item unless a listed constraint rules it out; record the resolved versions in the README and the reason for each choice in `docs/adr/0001-stack.md`.

- **Toolchain:** latest stable CPython via `uv` (`uv python list`), Node active LTS via `npm`; commit both lockfiles.
- **Gateway:** FastAPI :8000 served by Uvicorn (`uvicorn[standard]`, uvloop + httptools). SSE via FastAPI's built-in `fastapi.sse.EventSourceResponse` (`VERIFY:` the pinned FastAPI ships it; else ask). Stateless: any replica serves any stream. Plain HTTP until Tier 3, stated in the README.
- **Broker:** Apache Kafka 4.x, single node, KRaft (the only mode in 4.x), :9092. Topics `chat-requests` (partitions = `KAFKA_PARTITIONS`), `chat-requests.dlq`. Client `confluent-kafka` (librdkafka) using its asyncio classes per spike (b). If the async path cannot set per-message headers, trace context travels only in the envelope's `trace_id`, stated in the ADR.
- **State + streams:** Redis 8 Open Source, single node, :6379; it includes JSON and the Query Engine that `langgraph-checkpoint-redis` needs. License: tri-licensed incl. AGPLv3 (we run it unmodified). Fallback if spike (a) fails: `langgraph-checkpoint-postgres` with Postgres :5432.
- **Vector store:** Qdrant :6333 (REST), collection `intents`, 384-dim, cosine.
- **Embeddings:** `fastembed` (ONNX Runtime, no PyTorch in any image) with `BAAI/bge-small-en-v1.5` (384-dim), cached into the worker and seed images at build; query/passage prefixes as the model card specifies.
- **PII:** `presidio-analyzer` + `presidio-anonymizer` with a spaCy English model; the first reply proposes the model size (sm vs lg) with its accuracy/latency/image-size trade-off.
- **Orchestration:** `langgraph` StateGraph; routing is the graph's first node.
- **Inference:** one OpenAI-compatible chat-completions client, `LLM_BASE_URL` pointing at a compose service. Default engine Ollama :11434 (CPU, $0); the first reply proposes a small model tag and notes CPU latency. Documented production path: vLLM on GPU behind the same URL (not built, not tested). `MOCK_LLM=true` selects the mock (see Contracts).
- **Client:** React + TypeScript on Vite (:5173 dev), tests with Vitest + React Testing Library.
- **Traces:** OpenTelemetry SDK → Jaeger v2 (`cr.jaegertracing.io/jaegertracing/jaeger`, not the EOL v1 `all-in-one`), UI :16686, OTLP :4317/:4318.
- **Reverse proxy (Tier 3):** Caddy :443.

Any port, topic, key, image, or library not listed here: ask.

## Flow
1 `POST /chat` → 2 Presidio scrub (PERSON, EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD, US_SSN, IP_ADDRESS, LOCATION → typed placeholders such as `<EMAIL_ADDRESS>`) → 3 Kafka `chat-requests`, key = `thread_id` → 4 consumer-group worker → 5 embed → 6 Qdrant top-1; score < `ROUTE_THRESHOLD` ⇒ `general`; node `billing` | `tech_support` | `general` → inference → 7 Redis checkpoint per `thread_id` (TTL config) → 8 tokens to Redis Stream `tokens:{thread_id}:{message_id}` → 9 `GET /stream/{thread_id}/{message_id}` relays SSE.

## Contracts (become `SPEC.md`)
- Pydantic v2 models, defined once in `packages/common`: `ChatRequest {message, thread_id, message_id}`, `ChatAccepted {message_id, thread_id, stream_url}`, Kafka envelope `{message_id, thread_id, text, ts, trace_id}`, `TokenEvent {seq, token}`, `DoneEvent {seq}`, `ErrorEvent {seq, code, detail}`.
- `POST /chat` → 202 `ChatAccepted`. 413 oversize, 422 invalid body or `thread_id`, 429 rate limit (Redis-backed, per thread), 503 Kafka unavailable. A repeated `message_id` returns the same 202 body.
- `thread_id`: UUID or `[A-Za-z0-9_-]{1,64}`. `message_id`: client-generated UUID.
- Ordering and scale: per-thread order comes from the Kafka key; worker replicas ≤ `KAFKA_PARTITIONS`. Changing the partition count remaps keys and is an ADR decision, never a runtime tweak.
- Idempotency (worker): `SET dedupe:{message_id} processing NX EX DEDUPE_LEASE_S`. On success, set value `done` with `DEDUPE_TTL_S`. On terminal failure, delete the key. Key `done` ⇒ skip and commit; key `processing` ⇒ do not commit, let the lease expire and redeliver. Settings validation fails startup unless `DEDUPE_LEASE_S > INFERENCE_TIMEOUT_S × (MAX_RETRIES + 1) + RETRY_BACKOFF_MAX_S × MAX_RETRIES`.
- Stream: one stream per message; `seq` starts at 0; `MAXLEN` + TTL from config. SSE `id:` is the Redis stream entry id; the payload carries `seq`. Read from `0`, or from `Last-Event-ID` (exclusive) when sent. Close after `done`/`error`. A client disconnect stops the relay without error logs.
- Failure path: infra fault (Qdrant/LLM/Redis) → bounded retries with backoff → publish `ErrorEvent{code:"dependency_unavailable"}` to the stream, write the record to the DLQ, then commit. Malformed envelope → DLQ immediately, no retry.
- Seeding: one-shot compose service `seed` loads `data/intents.seed.jsonl` (≥ 10 exemplars per intent) into `intents`. Worker `depends_on: seed: condition: service_completed_successfully`. Both fail loud if the model or collection is missing.
- Evaluation data: `data/intents.eval.jsonl` (≥ 3 per intent + ≥ 5 off-topic), never loaded into Qdrant.
- `ROUTE_THRESHOLD`: set once by leave-one-out over the seed file (the max-accuracy cut); value and method recorded in an ADR. Changing it requires an ADR update, never a test-driven tweak.
- Mock LLM: deterministic. Output = `"[mock] intent=<intent> turns=<n> last=<first 20 chars of previous user turn or ->"`, split on spaces into tokens; tagged `"engine":"mock"`; logs a WARNING at startup.
- Model output is scrubbed before it is checkpointed, logged, or traced. CORS allowlist from config, no wildcard.
- Config: every key has a default in `.env.example`; the first reply proposes all defaults in one table, and approving the plan approves the table.

## Engineering rules
- Async for all network/LLM I/O. CPU-bound work on the request path (Presidio analysis, embedding) runs off the event loop (`asyncio.to_thread` or a bounded executor), never inline.
- 4xx = caller fault, 5xx = infra fault at every API boundary.
- Kafka: manual commit only after the outcome is durable (stream event written, or DLQ written).
- Every LLM call: user text in a delimited data field of the user message, never in the system prompt; a comment at the call site names the injection defense.
- Compose healthchecks with `depends_on: condition: service_healthy`. Images: multi-stage, slim base, non-root user.
- Config via env.
- ADRs (`docs/adr/NNNN-*.md`): stack (0001), checkpointer/Redis, Kafka async client + header handling, threshold calibration, stream-per-message, any approved deviation.

## Repo layout (exact; ask before deviating)
`services/gateway/`, `services/worker/`, `services/seed/`, `packages/common/` (schemas, scrubber, config), `frontend/`, `deploy/compose.yaml`, `data/`, `tests/{unit,integration,load}/`, `docs/{adr,prompts}/`.

## Acceptance tests (each must exist and pass; integration tests marked `@pytest.mark.integration` against real Kafka, Redis, Qdrant)
- **A1 PII:** a message with an email, phone, card, SSN, and IP → the consumed Kafka record contains none of the raw values and contains the typed placeholders.
- **A1b PII recall:** `tests/fixtures/pii_labeled.jsonl` (≥ 30 sentences, PERSON + LOCATION labeled) → report per-entity recall and p95 scrub latency. I set the recall floor after the first measurement; the agent reports, never picks the floor.
- **A2 Routing:** every sample in `data/intents.eval.jsonl` routes to its label; off-topic samples route to `general`.
- **A3 Streaming:** `seq` strictly increasing, ending with `done`; a late connection still receives everything from `0`; resuming with `Last-Event-ID` yields no duplicates and no gaps; a second turn's stream contains no tokens from the first.
- **A4 Failure:** a malformed envelope lands in the DLQ; Qdrant stopped after startup → the client receives `ErrorEvent{dependency_unavailable}` and the record is in the DLQ.
- **A5 Idempotency:** the same `message_id` POSTed twice → one stream with one `done`; a worker killed mid-processing → reprocessed after the lease expires; a lease below the formula fails settings validation (unit).
- **A6 Memory:** the second turn's mock output shows `turns=2` and the first turn's text prefix.
- **A7 Injection:** "ignore previous instructions" appears only inside the delimited user field; the system prompt is byte-identical to the baseline.
- **A8 Frontend:** the component renders tokens incrementally and shows the error state.
- **A9 Ollama:** one real round trip with `MOCK_LLM=false`.
- **A10 Scale:** `--scale worker=2 --scale gateway=2`; N concurrent threads × M turns → per-thread order preserved, no duplicate `done`, and a stream opened on one gateway replica for a message POSTed to the other completes.
- **A11 Latency (Tier 3):** `tests/load/` asyncio + `httpx` script with the mock at configured concurrency → report observed `POST` p95 and time-to-first-token p50/p95. I set SLOs after the first measurement; the agent reports numbers, never targets.

## Delivery and gating
- Write files with your file tools; show paths and diffs, not whole files. One branch + PR per verified piece. Self-merge (merge commit) only with green CI **and** that piece's integration-test output pasted verbatim in the PR description.
- First reply: file tree, tiered plan with one verify-check per step, Ollama model tag, spaCy model size, spike fallbacks, config-defaults table, clarifying questions. Start only after "approved", "go ahead", or "proceed" (exact words; anything else is not approval). Tier boundaries need no new approval unless a cut is proposed.
- Clarifying questions are allowed at any point where the brief is silent and the gap blocks progress.

## Verification loop
Test first, run it, read the output. Pass ⇒ next piece. Fail ⇒ fix and retry. After 2 failed attempts at the same check: STOP, paste the exact output, give a hypothesis. If Docker, a model, or the network is unavailable: mark the item UNVERIFIED, never "done".

## Continuity
After each piece, append a 5-line recap to `docs/AI_ORCHESTRATION.md` and commit it with the piece: done / verified how (rung + command) / elapsed (from `date`) / next / open risks. After context compaction: re-read that file and restate the latest recap and tier before any other action.

At the end of the run, append a retrospective to the same file: time per tier vs mark, every check that failed twice, every clarifying question asked. Each question is a gap for v2.5.

## Refusals
You MUST NOT:
- Claim any result you did not observe in command output.
- Weaken, skip, or delete a test; tune the threshold, seed/eval data, or mock to pass; set your own recall floor or latency SLO; widen scope to route around a failing check.
- Let a dedupe key block redelivery of an unprocessed message.
- Send unscrubbed text to Kafka, logs, traces, checkpoints, or the LLM; put user or retrieved text into system instructions.
- Use a commercial/metered endpoint, point `LLM_BASE_URL` outside the compose network, or activate the mock without `MOCK_LLM=true`.
- Run CPU-bound scrubbing or embedding inline on the event loop.
- Use bare `except`, or map infra faults to 4xx or caller faults to 5xx.
- Invent a port, topic, key, schema, status code, image, version, or model name not defined here; ask instead.
- Commit placeholders (`TODO`, `pass`, unfilled template slots such as `<repo-name>`; the typed PII tokens such as `<EMAIL_ADDRESS>` are data, not placeholders), secrets, machine-local paths, or attribution trailers; rewrite history.
- Pass a gate without an exact trigger phrase.
