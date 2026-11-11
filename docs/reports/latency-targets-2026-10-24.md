# Latency targets, pre-registered before the 2026-10-24 rerun

Written and committed before any measurement in `docs/reports/latency-2026-10-24.md`, so the
results cannot shape the targets. These are **proposals**: the lead sets SLOs
(`docs/known_limitations.md`) and may replace them. The results are judged against these as
written.

Amended before any measurement (same day): queue delay was first defined from the end of the
gateway's `POST /chat` span. A single probe showed that span ending after the worker had already
started, so it is not the enqueue time. It is now measured from a new `kafka.produce` span (the broker
ack). The 500 ms threshold did not change.

Second amendment, also before any load: the probe trace showed `kafka.produce` running 2077 ms
while the worker started 1128 ms into it. The client learns of the broker ack about 1 s after the
worker already has the record, so the span's end is not the enqueue either. Queue delay is now
measured from the **start** of `kafka.produce`, when the gateway hands the record to its Kafka
client, to the start of `worker.process`. That includes the producer's client-side buffering, which a
user waits through. The 500 ms threshold did not change.

## Mock engine (`MOCK_LLM=true`)
Load: 200 turns, each on a fresh thread, 20 in flight, round-robin over 2 gateway replicas
(1 CPU / 1.5 GB each) and 2 worker replicas (2 CPU / 1.5 GB each). Development host: 12 logical
CPUs, 15.7 GB RAM, Docker VM 8.3 GB.

| Metric | Target | Why |
|---|---|---|
| Errors (non-202, stream `error` event, transport failure) | 0 of 200 | Nothing in this load is a caller fault or an injected outage |
| `POST /chat` p95 | ≤ 1000 ms | 1 s is the usual limit for an interaction to feel uninterrupted. A 202 only acknowledges the enqueue |
| Queue delay p95 (gateway `kafka.produce` span start → worker `worker.process` span start) | ≤ 500 ms | 20 users must not build a growing backlog on 2 workers |
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

## Addendum 2026-11-03: queue delay under an open-loop load (written before any measurement)
The mock-engine load above is a closed loop: 20 users, each sending its next turn the moment the last
one finishes. It keeps about 19 turns in the system (Little's law on the 2026-10-30 runs: 20.5–22.9
turns/s × 0.81–0.93 s mean completion) against 8 worker slots (2 replicas × `WORKER_CONCURRENCY` 4),
so about 11 always wait in Kafka, whatever the code does. The queue never drains, and the 500 ms target
can fail at any capacity. Its stated purpose is that the offered load must not build a growing backlog,
which a saturating closed loop cannot test. Decided by the lead in chat on 2026-11-03, fixed here before
any run:

- **Capacity** `X`: throughput of the closed-loop load above (200 turns, 20 in flight), measured in the
  same session as the open-loop runs, on the same stack.
- **Open-loop load**: 200 turns on fresh threads, arriving at rate `R = 0.75 × X` with exponential
  gaps (seeded, so both runs see the same schedule), round-robin over the 2 gateways, each turn
  independent of when earlier ones finish.
- **Judged under the open-loop load**: queue delay p95 ≤ 500 ms, and no growing backlog: the queue-delay
  p95 of the last 50 traced turns is at most twice that of the first 50. Errors, `POST /chat`, first
  token and completion are judged against the mock targets above, unchanged.
- **Reported, not judged**: the closed-loop queue delay, as the delay at saturation.
- **Runs**: a 20-turn warm-up, then closed loop, open loop, closed loop, open loop. Each open-loop run
  takes its `R` from the closed-loop run just before it. Both pairs are reported; the target passes only
  if both open-loop runs pass.
- **Stack**: as above (2 gateways at 1 CPU, 2 workers at 2 CPU, `WORKER_CONCURRENCY` 4), current `main`,
  no other compose project running, LangFuse keys not set (LLM traces off; Jaeger on, as queue delay is
  read from it).

## Addendum 2026-11-11: a five-minute open loop for the backlog check (written before any measurement)
Item 1.5 of `audit-followups-2026-11-06.md`. The 2026-11-03 open-loop runs failed "no growing backlog"
as written (last 50 traced turns' p95 3.6× and 4.7× the first 50). They lasted 14 seconds, and seed 0
offered about 64% of capacity in the first 50 turns and about 92% in the last 50, so a burst could not
be told from growth (`latency-2026-11-03-queue-delay.md`). No code changes before this run. Everything
in the 2026-11-03 addendum holds, except:

- **Duration:** each open-loop run sends `round(300 × R)` turns, about five minutes at `R = 0.75 × X`.
  On the seed-0 schedule at that size, the offered rate per tenth of the run varies by at most about
  17% (checked offline: 14.3–16.1 turns/s at R = 15, 17.5–20.2 at R = 19).
- **No growing backlog, judged:** the queue-delay p95 of the last tenth of the traced turns, in arrival
  order, is at most twice the p95 of the first tenth. The script reports the p95 of every tenth
  (`queue_delay_p95_by_tenth_ms`) and the rate the schedule offered in each (`offered_rate_by_tenth`).
  Both are published in full, but only the first-to-last comparison is the verdict.
- **Unchanged and judged:** overall queue-delay p95 ≤ 500 ms; errors, `POST /chat`, first token and
  completion against the mock targets above.
- **Runs:** a 20-turn warm-up, then closed loop (200 turns, 20 in flight), five-minute open loop,
  closed loop, five-minute open loop. Each open loop takes `R` from the closed loop just before it.
  The target passes only if both open-loop runs pass.
- **Stack:** the `chatbot` compose project rebuilt from `main` at `1ce514f` (which adds `audit-db`).
  2 gateways at 1 CPU, 2 workers at 2 CPU, `WORKER_CONCURRENCY` 4, `MOCK_LLM=true`, LangFuse keys
  unset, Jaeger on, and no other compose project running. The `chatbot` project also runs the
  frontend and Caddy, which serve no traffic during the runs.
- **If a run cannot be traced** (Jaeger loses traces at this volume), the run is reported as not
  judged. Fewer than 90% of successful turns traced counts as a loss.
