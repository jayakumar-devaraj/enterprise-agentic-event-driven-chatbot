# A11 — latency with the mock engine (reported, no SLO)

2026-10-17 19:11 CDT. 2 gateways (1 CPU / 1.5 GB each, caps from PR #19) + 2 workers, `MOCK_LLM=true`.
`uv run python tests/load/latency_a11.py --requests 200 --concurrency 20` → `docs/reports/a11-latency.json`:

| Metric | p50 | p95 |
|---|---|---|
| `POST /chat` | 3544.5 ms | 4822.4 ms |
| Time to first token (from POST start) | 4442.6 ms | 6807.0 ms |

Wall time 47.62 s for 200 turns.

Not verified, a hypothesis for the lead: POST time is dominated by queueing for the CPU-bound
Presidio scrub (5.72 ms p95 in isolation, `docs/reports/pii_recall.json`) on a gateway capped at 1
CPU with ~10 requests in flight per replica. Raising the gateway CPU cap or replica count is the
first experiment. The lead sets the SLOs; nothing here is tuned to a target.

## Follow-up investigation (19:24–20:07 CDT) — findings, no fix shipped

- **The 1-CPU gateway hypothesis is wrong.** Sampling `docker stats` every 3 s during a repeat run
  (POST p50 3609.9 ms / p95 5549.9 ms, TTFT p50 4465.8 / p95 6442.9 ms) showed both **workers pinned at
  their 2-CPU cap (130–207 %)** while gateways mostly stayed under 100 %.
- **Python-side buffering in the async producer.** `confluent_kafka.aio.AIOProducer` defaults to
  `batch_size=1000, buffer_timeout=1.0` s: messages wait in Python until 1000 accumulate or the 1 s
  timer fires, before librdkafka sees them. That adds up to ~1 s to every `POST /chat`.
- **Attempted fix, reverted:** `batch_size=1, buffer_timeout=0` broke delivery (POSTs returned 503 —
  with `buffer_timeout=0` the flush timer is never started). Reverted; POST 202 confirmed afterwards.
- **Next experiments, in order:** (1) `batch_size=1` with a small positive `buffer_timeout`
  (e.g. 0.01 s), re-run A11; (2) profile worker CPU per message (output scrub with `en_core_web_lg`,
  fastembed/ONNX thread spinning — try `threads=1` for the embedder) and raise worker replicas toward
  `KAFKA_PARTITIONS`.
