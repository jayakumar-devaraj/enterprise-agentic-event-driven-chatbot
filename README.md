# enterprise-agentic-event-driven-chatbot

An event-driven customer-support chatbot. `POST /chat` answers `202 Accepted` at once; Kafka-driven
workers produce the reply asynchronously and stream it back token by token over SSE from Redis
Streams, so a slow model never holds an HTTP request open and any gateway replica can serve any
stream.

- **Privacy:** user text is PII-scrubbed (Presidio) in the gateway before it reaches Kafka, the
  model, logs or traces; model output is scrubbed again before it is streamed.
- **Agentic routing:** a LangGraph `StateGraph` routes each turn by local embeddings + Qdrant to
  billing, technical support, general help, or a refusal when prompt injection is detected, with
  per-thread memory in a Redis checkpointer.
- **Human in the loop:** a refund request pauses the graph (`interrupt()`) until the thread owner
  calls `POST /approve`; a refund request is then appended to the Redis stream `refunds` at most
  once, atomically with its audit entry (no payment processor is integrated).
- **Reliability:** idempotent enqueue, dedupe leases, Kafka offsets committed only after a durable
  outcome, and a dead-letter queue plus an SSE `error` event when a dependency fails.
- **Operations:** HTTP Basic auth with thread ownership, per-thread rate limits, OpenTelemetry traces
  to Jaeger and LLM traces to LangFuse, a Redis `audit:events` trail, a React chat window, and Caddy
  TLS in front; runs at $0 on CPU with Ollama or a deterministic mock.

Contracts: `SPEC.md`. Summary for leadership: `docs/summary.md`. Known gaps:
`docs/known_limitations.md`.

## Tech stack

Versions read from `pyproject.toml` / `uv.lock` / `frontend/package.json`.

- **Language / tooling:** Python 3.13.13 (`.python-version`), uv workspace (`packages/common`,
  `services/{gateway,worker,seed}`), build backend `uv_build` 0.13.x.
- **API:** FastAPI 0.143.3 on Starlette 1.8.0, served by uvicorn 0.54.0 (`[standard]`: httptools;
  uvloop on Linux) — `uvicorn gateway.app:create_app --factory`; `POST /chat`, `GET /stream/{thread_id}/{message_id}` (SSE via `fastapi.sse.EventSourceResponse`,
  `Last-Event-ID` resume), `POST /approve/{thread_id}/{message_id}` (human approval of a paused refund,
  LangGraph `interrupt()` / `Command(resume=...)`), `GET /healthz` (liveness), `GET /readyz` (Redis + Kafka readiness).
- **Security (access control):** HTTP Basic against `AUTH_USERS` (stdlib scrypt, run once per accepted pair per process; generate entries with
  `uv run python -m gateway.auth <user> <password>`), thread ownership bound in Redis (401/403).
- **Validation:** pydantic 2.14.1 (wire contracts in `chat_common.schemas`, strict, extra fields
  rejected), pydantic-settings 2.16.0 (`chat_common.settings`; startup fails on an unsafe dedupe lease).
- **Security (PII):** presidio-analyzer 2.2.366 + presidio-anonymizer 2.2.366 with spaCy 3.8.16 and
  `en_core_web_lg` 3.8.0 (`chat_common.scrubber`; typed placeholders such as `<EMAIL_ADDRESS>`,
  `ascrub` runs off the event loop).
- **Persistence:** redis-py 8.1.0 (`redis.asyncio`; `audit:events` stream via `chat_common.audit`).
- **Audit export (profile `audit`):** `services/audit` (psycopg 3.3.6) reads `audit:events` through a Redis consumer group and inserts into the append-only `audit_log` table of `postgres:16.14` (`audit-db`) with the stream entry id as the idempotency key; it acknowledges only after the insert commits. The exporter's role can `SELECT` and `INSERT` only. Query it with SQL, for example `docker compose -f deploy/compose.yaml exec audit-db psql -U audit_admin -d audit -c "select ts, event, actor from audit_log where thread_id = '<id>' order by id"`; start it with `AUDIT_DB_PASSWORD=<pw> AUDIT_WRITER_PASSWORD=<pw> docker compose -f deploy/compose.yaml --profile audit up -d --build audit-db audit-exporter`.
- **Observability (traces):** opentelemetry-sdk 1.45.1 + OTLP/gRPC exporter 1.45.1 →
  Jaeger v2 (`chat_common.tracing`; gateway auto-instrumented by
  opentelemetry-instrumentation-fastapi 0.66b1; trace context crosses Kafka as the W3C `traceparent`
  in `Envelope.trace_id`); langfuse 5.0.2 sends one scrubbed `generation` per reply to LangFuse
  Cloud, linked by trace id (`worker.observability`, disabled when keys are absent).
- **Observability (logs):** stdlib `logging` with `chat_common.logjson.JsonFormatter` (one JSON object per
  line, `trace_id`/`thread_id` as keys).
- **Orchestration:** langgraph 1.2.16 with langgraph-checkpoint-redis 0.5.2 (`AsyncRedisSaver`;
  checkpoint write/read on Redis 8 proven by spike a, ADR 0002).
- **Embeddings / vector search:** fastembed 0.9.0 (ONNX Runtime 1.31.0, `BAAI/bge-small-en-v1.5`,
  query instruction on messages only) and qdrant-client 1.19.1 (worker searches over gRPC with
  `prefer_grpc`; seed loads over REST). Thresholds from leave-one-out calibration (ADR 0004).
- **Model providers:** openai 3.33.0 `AsyncOpenAI` against the OpenAI-compatible `LLM_BASE_URL`
  (Ollama in compose; vLLM is the production path behind the same URL); the deterministic mock engine
  runs only with `MOCK_LLM=true`. Worker entry point: `python -m worker.main`.
- **Messaging:** confluent-kafka 2.16.0 (`confluent_kafka.aio` producer/consumer, manual commits;
  no per-message headers on the async producer, ADR 0003).
- **Infrastructure (`deploy/compose.yaml`):** `apache/kafka:4.3.1` (KRaft) + one-shot topic init,
  `redis:8.10.2`, `qdrant/qdrant:v1.19.2`, `cr.jaegertracing.io/jaegertracing/jaeger:2.22.0`,
  `ollama/ollama:0.40.6` (profile `llm`, model baked in at build). Host ports bind to 127.0.0.1 on
  19092 / 16379 / 16333 / 16334 / 16686 / 14317; Kafka advertises `127.0.0.1` (not `localhost`) to
  avoid IPv6-first resolution delays.
- **Document questions:** a separate RAG service (its own repository, container and Postgres) is
  called over HTTP by `worker/rag_client.py` (httpx, one shared pool, one deadline) and run from a
  pinned image behind the compose profile `rag`; its contract is `docs/contracts/rag-openapi-v1.json`.
  `USER_TENANTS` (gateway) binds users to tenants; the worker authenticates to the RAG service with `RAG_TENANT_TOKENS` or, preferably, per-tenant OAuth client credentials (`RAG_TOKEN_URL` + `RAG_TENANT_CLIENTS`, ADR 0011).
- **Testing:** httpx 0.28.1 (`ASGITransport` for in-process API tests), pytest 9.1.1 (unit by default; `-m integration` for real-service tests),
  pytest-asyncio 1.4.0 (`asyncio_mode=auto`), pytest-cov 7.1.0 (coverage reports).
- **Lint:** ruff 0.17.1 (`ruff check`, `ruff format --check`).
- **Frontend (`frontend/`, versions from `package.json`):** React 19.3.1 + react-dom 19.3.1,
  TypeScript 6.0.3, Vite 8.3.4 with @vitejs/plugin-react 6.1.2 (dev server on
  :5173, `/api` proxied to the gateway); tests with Vitest 5.0.5, @testing-library/react
  16.3.3 and jsdom 30.1.3. Built and tested in `node:24.22.0-slim` (Node 24 LTS);
  SSE is read with `fetch` so the Basic `Authorization` header can be sent.
- **CI:** GitHub Actions (`actions/checkout` v7.0.1, `astral-sh/setup-uv` v10.3.0): lint + unit,
  integration against the compose stack, and frontend type-check + Vitest on every push and PR.

## Architecture

`POST /chat` → gateway (Basic auth, thread ownership, validation, rate limit, Presidio scrub) →
Kafka `chat-requests` keyed by `thread_id` → worker (dedupe lease → LangGraph: route by fastembed +
Qdrant → billing / tech_support / general / refusal / refund approval gate → LLM or mock → output
scrub) → Redis Stream `tokens:{thread}:{message}` → `GET /stream/...` SSE → React chat window.
Diagrams and failure modes: `docs/architecture/HLD.md`, `docs/architecture/LLD.md`; contracts:
`SPEC.md`.

## Quickstart

Prerequisites: Docker, uv, network access for first-time model downloads (spaCy, fastembed).

```bash
uv sync                                                    # Python 3.13.13 + all packages
docker compose -f deploy/compose.yaml up -d --wait kafka redis qdrant jaeger
docker compose -f deploy/compose.yaml up --exit-code-from kafka-init kafka-init
QDRANT_URL=http://127.0.0.1:16333 uv run python -m seed.main

# one terminal each (host endpoints; MOCK_LLM=true is the default)
export KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:19092 REDIS_URL=redis://127.0.0.1:16379/0   QDRANT_URL=http://127.0.0.1:16333 QDRANT_GRPC_PORT=16334   OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:14317   AUTH_USERS="demo:$(uv run python -m gateway.auth demo demo-pw | cut -d: -f2-)"
uv run python -m worker.main
uv run uvicorn gateway.app:create_app --factory --host 0.0.0.0 --port 8000

docker compose -f deploy/compose.yaml up -d --build frontend   # http://localhost:5173 (demo / demo-pw)
```

Document questions (optional): needs Ollama running (`--profile llm`) for the RAG service's sufficiency check.

```bash
export RAG_TENANT_TOKENS=<token>:T1 RAG_DB_PASSWORD=<password>   # placeholders; set USER_TENANTS=demo:T1 on the gateway
docker compose -f deploy/compose.yaml --profile rag up -d rag-db rag-api   # http://127.0.0.1:18000/health
docker compose -f deploy/compose.yaml --profile rag up rag-seed            # uploads deploy/rag/handbook.txt as tenant T1
```

The image is `RAG_API_IMAGE` (default: the pinned `ghcr.io/jayakumar-devaraj/multi-tenant-agentic-rag:v0.1.0`).
`uv run pytest -m rag` shows the settings the host-run worker needs (`tests/integration/test_rag_e2e.py`).

Traces: Jaeger UI at http://localhost:16686. LangFuse: set `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` in the environment (never commit them).

## Local development

- Full stack in containers (Task 12 images; gateway on a host port from 127.0.0.1:8000–8003, see
  `docker compose -f deploy/compose.yaml port gateway 8000`; the chat UI at :5173 proxies to it):
  `export AUTH_USERS="$(uv run python -m gateway.auth demo demo-pw)"` then
  `docker compose -f deploy/compose.yaml up -d --build --wait`. On Windows Git Bash also export
  `MSYS_NO_PATHCONV=1`, or the `/` segments of the scrypt hash are rewritten into Windows paths.

- Config: every key and default is in `.env.example`; settings come from the environment.
- State: Redis (AOF, `everysec`) and Kafka keep their data on the named volumes `redis-data` and
  `kafka-data`; `down` keeps them, `down -v` deletes them (ADR 0008). Redis backup and restore:
  `docker compose -f deploy/compose.yaml stop redis`, then
  `docker run --rm -v chatbot_redis-data:/data:ro -v "$PWD":/backup redis:8.10.2 tar -C /data -cf /backup/redis.tar .`;
  restore by extracting into an empty `chatbot_redis-data` volume (`tar -C /data -xf`) before
  starting Redis again. After a restore, replay what the backup missed (SPEC §12): with the gateways
  and workers stopped (`docker compose -f deploy/compose.yaml stop gateway worker`), run
  `docker compose -f deploy/compose.yaml exec -T kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server kafka:9092 --group chat-workers --topic chat-requests --reset-offsets --to-earliest --execute`,
  start the workers, wait until
  `kafka-consumer-groups.sh --bootstrap-server kafka:9092 --describe --group chat-workers` shows a
  `LAG` of 0, then start the gateways.
- Dead-letter queue: `docker compose -f deploy/compose.yaml exec worker python -m worker.dlq list`
  prints one JSON line per record (no message text); `... python -m worker.dlq replay --offset 3 7`
  or `replay --all` re-produces the failed envelopes (SPEC §7). From the host, run it with
  `KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:19092 REDIS_URL=redis://127.0.0.1:16379/0 uv run python -m worker.dlq list`.
- HTTPS entry point (Tier 3): `docker compose -f deploy/compose.yaml up -d --wait caddy` →
  https://localhost:8443 (UI) and https://localhost:8443/api/... (gateway), HTTP/2, Caddy internal CA.
  The direct dev ports (gateway 8000–8003, Vite 5173) are plain HTTP on 127.0.0.1.
- Real inference instead of the mock (A9, `llama3.2:1b` baked into the Ollama image):
  `MOCK_LLM=false docker compose -f deploy/compose.yaml --profile llm up -d --build --wait`.
- Generate a user: `uv run python -m gateway.auth <user> <password>` → append to `AUTH_USERS`.
- End-to-end demo: `uv run python scripts/end_to_end_demo.py ["<question>"]` finds the compose
  gateway (starting the stack with `docker compose up -d --wait` if it is down), posts the question
  and prints the streamed reply; `--base-url` skips the stack check, `--debug` shows tracebacks.
  The same command works from CMD, PowerShell or a POSIX shell, from any directory with
  `uv run --project <root> python <root>/scripts/end_to_end_demo.py` (verified on Windows only).

## Testing

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest                      # unit tests (integration excluded by default)
uv run pytest -m integration       # needs the compose stack + seed from Quickstart
uv run pytest -m "not scale and not llm and not durability and not stack" --cov=chat_common --cov=gateway --cov=worker --cov=seed
uv run pytest -m durability   # own compose project: SIGKILL, replacement, backup/restore, A4 Qdrant outage, A5 worker SIGKILL
uv run pytest -m scale     # A10: stack scaled to 2 gateways + 2 workers
uv run pytest -m llm       # A9: stack with MOCK_LLM=false and --profile llm
uv run pytest -m stack     # running stack: raw PII absent from logs, Kafka, DLQ, Redis and traces
uv run pytest -m rag       # document questions end to end: compose profile rag + Ollama (IT_RAG_URL, IT_RAG_TOKENS, IT_LLM_URL)
uv run python tests/load/latency_a11.py --requests 200 --concurrency 20   # A11 report
docker run --rm -v "$PWD/frontend":/app -w /app node:24.22.0-slim sh -c "npm ci && npm test"
```

Acceptance-test map and results: `docs/master_plan.md`, `docs/reports/`.

## Deployment / CI

`.github/workflows/ci.yml` runs lint + unit, integration (compose stack + seed), durability (Redis
and Kafka restart/replacement/restore), and frontend jobs on every pull request and on every push to `main`. Gateway, worker and seed images build from `services/*/Dockerfile`
(multi-stage, non-root, models baked in) and run with the whole stack via `deploy/compose.yaml`;
there is no registry push or production deployment target yet.
