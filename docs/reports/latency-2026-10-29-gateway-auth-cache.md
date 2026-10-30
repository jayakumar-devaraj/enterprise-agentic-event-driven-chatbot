# Gateway CPU: scrypt on every request, 2026-10-29

Follows `latency-2026-10-29-embedder-threads.md`. With the worker queue fixed, the two 1-CPU gateways
became the bottleneck. Targets are those of `latency-targets-2026-10-24.md`; none was changed.

## Cause
- Both gateways ran at 95–97% CPU under the A11 mock load (`docker stats`, sampled every 3 s), while the workers
  used 25–55%.
- In the Jaeger traces of that run, `POST /chat` p50 was 1804 ms, of which `kafka.produce` was 75 ms. The rest
  is untraced gateway work.
- A py-spy 0.4.2 profile of one gateway during the same load (25 s at 50 Hz, attached from a sidecar sharing its
  PID namespace) attributed **50.9%** of samples inclusively to `gateway.auth.verify` (scrypt) and 32.5% to the
  Presidio/spaCy scrub.
- scrypt takes 28.5 ms of CPU per check in a 1-CPU container of the gateway image (20 calls), and it ran on every
  `POST /chat` and every `GET /stream`, so twice per turn for the same pair.

## Fix
`gateway.auth.Verifier` remembers pairs it has accepted, keyed by an HMAC under a per-process random key, for the
life of the process (`AUTH_USERS` is read once at startup). Failures are never cached, so every wrong guess still
pays scrypt.

## Measurement
Same stack and load as the previous report (2 gateways at 1 CPU, 2 workers at 2 CPU with the one-thread
embedder, mock engine, 200 turns, 20 in flight, warm-up first). One run each; raw samples in
`latency-2026-10-29-before-auth-cache.json` and `latency-2026-10-29-gateway-auth-cache.json`.

| Metric | Target | Before | With the cache | Verdict |
|---|---|---|---|---|
| Errors | 0 | 0 | 0 | Pass |
| `POST /chat` p50 / p95 | p95 ≤ 1000 ms | 1583 / 3276 ms | 77 / **823 ms** | **Pass** |
| Queue delay p95 | ≤ 500 ms | 154 ms | 2010 ms | **Fail** |
| First token p95 | ≤ 2000 ms | 4488 ms | 2200 ms | **Fail** |
| Completion p95 | ≤ 3000 ms | 4867 ms | **2221 ms** | **Pass** |
| Throughput | reported | 8.31 turns/s | 14.55 turns/s | — |

## What remains
Gateways now run at about 50% CPU and the queue is back in the workers (queue delay p95 2010 ms). Each worker
process used about one of its two CPUs (`docker stats` 104–111%): a worker handles one record at a time, so it
cannot use a second core. That is the next change, untested.
