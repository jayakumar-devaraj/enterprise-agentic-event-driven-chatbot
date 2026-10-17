# 0001 · Stack

**Status:** Accepted 2026-10-17 (Gate A, `docs/design_dialogue.md` Round 4).

## Context
The brief fixes most of the stack (FastAPI, Kafka 4 KRaft, Redis 8, Qdrant, fastembed, Presidio,
LangGraph, OpenAI-compatible inference, OpenTelemetry → Jaeger v2, React/Vite). Open axes were the
checkpointer, spaCy model size, Ollama model, eval tooling, logging, Python and TypeScript lines.
Three bundles were proposed; the lead locked Bundle 1 and then changed several items.

## Decision
Versions resolved live from PyPI, npm, Docker Hub and the Ollama registry on 2026-10-17 (none from
memory):

| Item | Version | Reason |
|---|---|---|
| Python | 3.13.13 (uv 0.11.19) | Lead choice over 3.14.5 for wider wheel coverage |
| FastAPI / uvicorn | 0.143.3 / 0.54.0 | Brief; latest stable |
| confluent-kafka / Kafka | 2.16.0 / `apache/kafka:4.3.1` | Brief; latest stable |
| Redis / redis-py | `redis:8.10.2` / 8.1.0 | Brief; includes JSON + Query Engine needed by the checkpointer |
| langgraph / checkpoint-redis | 1.2.16 / 0.5.2 | Brief; Redis keeps one stateful store (ADR 0002) |
| Qdrant / client | `v1.19.2` / 1.19.1 | Brief |
| fastembed | 0.9.0, `BAAI/bge-small-en-v1.5` | Brief; ONNX, no PyTorch |
| Presidio / spaCy | 2.2.364 / 3.8.16 + `en_core_web_lg` 3.8.0 | Lead chose `lg` for PERSON/LOCATION recall over image size |
| openai / Ollama | 3.33.0 / `ollama/ollama:0.40.6` `llama3.2:1b` | One OpenAI-compatible client; lead chose the 1B model |
| OpenTelemetry / Jaeger | 1.45.1 / 2.22.0 | Brief (fixed standard) |
| langfuse | 5.0.2, LangFuse Cloud US | Lead choice (ADR 0006) |
| Logging | stdlib `logging` + JSON formatter | Lead choice over structlog; no dependency |
| Eval | DeepEval 4.3.1, dev group only | Lead choice; no PyTorch; telemetry opted out |
| Test / lint | pytest 9.1.1, pytest-asyncio 1.4.0, pytest-cov 7.1.0, ruff 0.17.1 | Latest stable |
| Frontend | Node 24.22.0 LTS, React 19.3.1, TypeScript 6.0.3, Vite 8.3.4, Vitest 5.0.5 | TS 6 because typescript-eslint 8.71.2 peers `<6.1.0` |

## Consequences
- Rejected: llm-guard (needs Python < 3.13 and PyTorch).
- APIs not yet exercised are marked `VERIFY:` in code until a test covers them: `fastapi.sse`,
  confluent-kafka asyncio classes, `AsyncRedisSaver`, openai 3.x, langfuse 5.x env names.
- `en_core_web_lg` and `llama3.2:1b` enlarge images/model pulls; CI uses `MOCK_LLM=true`.
