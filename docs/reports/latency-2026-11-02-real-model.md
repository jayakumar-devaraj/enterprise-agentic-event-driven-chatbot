# Worker concurrency against a real model, 2026-11-02

Follows `latency-2026-10-30-worker-concurrency.md`, which measured `WORKER_CONCURRENCY` against the mock engine
and a fake remote model, and left the real model server unmeasured (ADR 0012).

## Method
Stack: an isolated compose project (`chatbot-llm`) from this branch with `MOCK_LLM=false` and profile `llm`:
Ollama with `llama3.2:1b` on CPU (no GPU; compose limit 4 CPUs, 3 GiB), 2 workers at 2 CPUs, 2 gateways at
1 CPU, on one Docker VM with 12 vCPUs and 8 GB. No other stack ran during the measurements. LangFuse keys were
not set (no LLM traces); Jaeger traces were on, as they are what queue delay is read from.

Three configurations, set with a scratch compose override that was not committed:

| Run | `WORKER_CONCURRENCY` | `OLLAMA_NUM_PARALLEL` |
|---|---|---|
| A | 1 | 1 (Ollama's default) |
| B | 4 (the default) | 1 |
| C | 4 | 4 |

Each run: Ollama and the workers recreated with the configuration, a 4-turn warm-up, then
`tests/load/latency_a11.py --requests 16 --concurrency 4 --engine ollama`. One run per configuration, 16 turns
each: differences of a few tenths of a turn per second are inside what one run can show. Raw samples:
`latency-2026-11-02-real-model-{A,B,C}.json`.

## Results
p95 unless noted, milliseconds:

| Metric | A | B | C |
|---|---|---|---|
| Errors | 0 | 0 | 0 |
| Throughput (turns/s) | 0.23 | 0.18 | 0.24 |
| Queue delay p50 | 7971 | 21 | 21 |
| Queue delay p95 | 16661 | 85 | 40 |
| First token p50 | 14565 | 15988 | 4593 |
| First token p95 | 20554 | 36925 | 21498 |
| Completion p50 | 15890 | 21585 | 11278 |
| Completion p95 | 24562 | 39013 | 43118 |
| `POST /chat` p50 | 42 | 43 | 58 |
| Empty replies | 1 | 1 | 1 |

## Reading
- The model server is the bottleneck. Throughput is about 0.2 turns/s in all three runs: 4 CPUs of llama.cpp
  produce the same tokens per second whether they serve one request or four.
- Worker concurrency alone (B) moves the wait from Kafka into Ollama's queue: queue delay falls from 8 s to 21 ms,
  but first-token and completion latency rise, because four requests wait at a server that answers one at a time.
- Sizing Ollama for the workers (C) brings the median first token down to 4.6 s, but the 4 CPUs are now shared by
  up to 4 generations, so completion p95 is the worst of the three (43 s) and throughput does not move.
- The 4× gain of 2026-10-30 holds only when the model server has spare capacity: concurrent calls that do not slow
  each other (the fake model, or a GPU-backed or hosted endpoint). On a saturated server it buys no throughput.
- One reply per run finished with `done` and no token; empty replies against the real model were already seen on
  2026-10-24 and are still not investigated.
