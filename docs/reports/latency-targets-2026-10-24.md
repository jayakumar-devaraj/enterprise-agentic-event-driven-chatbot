# Latency targets, pre-registered before the 2026-10-24 rerun

Written and committed before any measurement in `docs/reports/latency-2026-10-24.md`, so the
results cannot shape the targets. These are **proposals**: the lead sets SLOs
(`docs/known_limitations.md`) and may replace them. The results are judged against these as
written.

Amended before any measurement (same day): queue delay was first defined from the end of the
gateway's `POST /chat` span. A single probe showed that span ending after the worker had already
started, so it is not the enqueue time. It is now measured from a new `kafka.produce` span (the broker
ack). The 500 ms threshold did not change.

## Mock engine (`MOCK_LLM=true`)
Load: 200 turns, each on a fresh thread, 20 in flight, round-robin over 2 gateway replicas
(1 CPU / 1.5 GB each) and 2 worker replicas (2 CPU / 1.5 GB each). Development host: 12 logical
CPUs, 15.7 GB RAM, Docker VM 8.3 GB.

| Metric | Target | Why |
|---|---|---|
| Errors (non-202, stream `error` event, transport failure) | 0 of 200 | Nothing in this load is a caller fault or an injected outage |
| `POST /chat` p95 | ≤ 1000 ms | 1 s is the usual limit for an interaction to feel uninterrupted. A 202 only acknowledges the enqueue |
| Queue delay p95 (gateway `kafka.produce` span end, the broker ack → worker `worker.process` span start) | ≤ 500 ms | 20 users must not build a growing backlog on 2 workers |
| Time to first token p95 (from POST start) | ≤ 2000 ms | No model latency with the mock, so this is pure system overhead |
| Completion p95 (POST start → `done`) | ≤ 3000 ms | The mock reply is about 10 tokens, so completion should follow the first token closely |
| Throughput | reported, no target | Bounded queue delay at the offered load is the capacity criterion |

## Real model (`MOCK_LLM=false`, Ollama `llama3.2:1b`, CPU only, 4 CPU / 3 GB)
Load: 20 turns, fresh threads, 2 in flight, same replicas.

| Metric | Target | Why |
|---|---|---|
| Errors | 0 of 20 | As above |
| Time to first token p95 | ≤ 10 s | A 1B model on CPU. Prompt processing dominates, and a user still sees progress within 10 s |
| Completion p95 | ≤ 60 s | `INFERENCE_TIMEOUT_S` is 60 s, so a slower reply fails anyway |
| `POST /chat` p95, queue delay p95 | same as the mock | Inference does not touch the accept path. Queueing is reported to show whether 2 users already saturate it |
