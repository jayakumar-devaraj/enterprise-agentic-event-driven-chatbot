# Worker queue delay: ONNX Runtime thread oversubscription, 2026-10-29

Follows experiment 3 of `latency-2026-10-24.md` ("where worker CPU goes outside spans"). Targets
are those of `latency-targets-2026-10-24.md`; none was changed.

## Cause
fastembed's ONNX Runtime session starts one intra-op thread per host core. `os.cpu_count()` in the
worker container reports the host's 12 cores while compose caps the container at 2 CPUs, and idle
ONNX Runtime threads spin. Measured in a fresh `--cpus 2` container of the worker image, 30 calls
each after 5 warm-up calls (process CPU from `getrusage`):

| Call | Default threads: wall / CPU | `threads=1`: wall / CPU |
|---|---|---|
| Query embedding | 40.5 / 82.2 ms | 10.1 / 10.1 ms |
| Scrub the user message | 29.0 / 54.5 ms | 3.9 / 3.9 ms |
| Scrub the reply | 32.0 / 62.2 ms | 5.3 / 5.3 ms |

The scrubber does not use ONNX Runtime; it is slower only because the spinning threads in the same
process take its CPU. This is the CPU the 2026-10-24 report saw outside traced spans.

## Fix
`worker.embedding.Embedder` creates its `TextEmbedding` with `threads=1`.

## A/B under load
Same stack and load for both runs, back to back on an otherwise idle host: 2 gateways, 2 workers
(2 CPU each), mock engine, `tests/load/latency_a11.py --requests 200 --concurrency 20`, each run
preceded by a 20-request warm-up and a fresh worker build. Raw samples:
`latency-2026-10-29-main.json` and `latency-2026-10-29-embedder-1thread.json`.

| Metric | Target | `main` (`940229f`) | `threads=1` | Verdict |
|---|---|---|---|---|
| Errors | 0 | 0 | 0 | Pass |
| `POST /chat` p50 / p95 | p95 ≤ 1000 ms | 76 / 1428 ms | 1644 / 3277 ms | **Fail (worse)** |
| Queue delay p95 | ≤ 500 ms | 5654 ms | **211 ms** | **Pass** |
| First token p95 | ≤ 2000 ms | 6121 ms | 4304 ms | Fail |
| Completion p95 | ≤ 3000 ms | 6131 ms | 4638 ms | Fail |
| Throughput | reported | 4.79 turns/s | 8.17 turns/s | — |

## What remains
The worker queue is gone, and the bottleneck moved to the gateways: with replies arriving sooner,
`POST /chat` p50 rose from 76 ms to 1644 ms. Not yet measured. A candidate is the scrypt check
(~150 ms of CPU) that runs on every `POST /chat` and every `GET /stream` request, against 1-CPU
gateways. One run each; no repeat runs were made to estimate run-to-run spread.
