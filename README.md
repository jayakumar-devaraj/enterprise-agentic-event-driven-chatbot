# enterprise-agentic-event-driven-chatbot

## Tech stack

Versions read from `pyproject.toml` / `uv.lock`. Only what is installed today is listed; each task adds
its dependencies here in the same commit.

- **Language / tooling:** Python 3.13.13 (`.python-version`), uv workspace (`packages/common`,
  `services/{gateway,worker,seed}`), build backend `uv_build` 0.13.x.
- **API:** FastAPI 0.143.3 on Starlette 1.8.0, served by uvicorn 0.54.0 (`[standard]`: httptools;
  uvloop on Linux) — `uvicorn gateway.app:create_app --factory`; `POST /chat`, `GET /stream/{thread_id}/{message_id}` (SSE via `fastapi.sse.EventSourceResponse`,
  `Last-Event-ID` resume), `POST /approve/{thread_id}/{message_id}` (human approval of a paused refund,
  LangGraph `interrupt()` / `Command(resume=...)`), `GET /healthz`.
- **Security (access control):** HTTP Basic against `AUTH_USERS` (stdlib scrypt; generate entries with
  `uv run python -m gateway.auth <user> <password>`), thread ownership bound in Redis (401/403).
- **Validation:** pydantic 2.14.1 (wire contracts in `chat_common.schemas`, strict, extra fields
  rejected), pydantic-settings 2.16.0 (`chat_common.settings`; startup fails on an unsafe dedupe lease).
- **Security (PII):** presidio-analyzer 2.2.364 + presidio-anonymizer 2.2.364 with spaCy 3.8.16 and
  `en_core_web_lg` 3.8.0 (`chat_common.scrubber`; typed placeholders such as `<EMAIL_ADDRESS>`,
  `ascrub` runs off the event loop).
- **Persistence:** redis-py 8.1.0 (`redis.asyncio`; `audit:events` stream via `chat_common.audit`).
- **Observability:** stdlib `logging` with `chat_common.logjson.JsonFormatter` (one JSON object per
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
- **Testing:** httpx 0.28.1 (`ASGITransport` for in-process API tests), pytest 9.1.1 (unit by default; `-m integration` for real-service tests),
  pytest-asyncio 1.4.0 (`asyncio_mode=auto`), pytest-cov 7.1.0 (coverage reports).
- **Lint:** ruff 0.17.1 (`ruff check`, `ruff format --check`).
- **CI:** GitHub Actions (`actions/checkout` v7.0.1, `astral-sh/setup-uv` v10.3.0) running lint and
  unit tests on every push and pull request.
