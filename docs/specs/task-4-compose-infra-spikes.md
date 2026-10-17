# Task 4 — Compose infra and spikes

**Purpose:** `deploy/compose.yaml` with Kafka (KRaft), topic init, Redis 8, Qdrant, Ollama, Jaeger v2,
healthchecks. Spike (a): pinned `langgraph-checkpoint-redis` writes and reads a checkpoint on
`redis:8.10.2`. Spike (b): pinned `confluent-kafka` asyncio producer/consumer import path and
per-message headers. ADRs 0001 (stack), 0002 (checkpointer/Redis), 0003 (Kafka async client + headers).
**Verify (integration):** `docker compose up -d --wait` healthy; spike tests marked
`@pytest.mark.integration` pass against the real services. Spike failure ⇒ pre-declared fallback +
ADR (Postgres checkpointer; trace context only in envelope `trace_id`).
