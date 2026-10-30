# Worker concurrency, 2026-10-30

Follows `latency-2026-10-29-gateway-auth-cache.md`, which ended with each worker using about one of its two
CPUs because it handled one Kafka record at a time. Targets are those of `latency-targets-2026-10-24.md`; none
was changed.

## Change
A worker runs up to `WORKER_CONCURRENCY` records at once (default 4). Records of one thread (the Kafka key) still
run one at a time in offset order, and a partition's offset is committed only below the oldest record whose
outcome is not yet durable (ADR 0012).

## Method
Stack: 2 gateways at 1 CPU, 2 workers at 2 CPU, the LangFuse keys from the developer `.env` (LLM traces on, as in
the earlier reports), `AUTH_USERS` exported for the demo user. Each run: fresh worker build, 20-turn warm-up, then
`tests/load/latency_a11.py --concurrency 20`. Runs alternate `main` (`08c7522`) and the branch, so drift on the
shared host affects both. Raw samples: `latency-2026-10-30-concurrency-{ab,io}_{main,fix}{1,2}.json`.

Two engines:
- **Mock** (`MOCK_LLM=true`, 200 turns): no I/O wait; every stage is CPU on the one Docker VM.
- **Fake remote model** (`MOCK_LLM=false`, 100 turns): `tests/load/fake_llm.py`, an OpenAI-compatible server on the
  compose network that waits 1 s, then streams 16 words 50 ms apart (about 1.8 s per reply), with
  `LLM_BASE_URL=http://fakellm:8080/v1`. This models a model call the worker waits on. The JSON field `engine`
  reads `mock` in these files because it is only the script's `--engine` label, which was not passed.

## Results
Mock engine, p95 unless noted (two runs each):

| Metric | Target | `main` | Concurrency 4 |
|---|---|---|---|
| Errors | 0 | 0 / 0 | 0 / 0 |
| Queue delay p50 | — | 658 / 734 ms | 421 / 308 ms |
| Queue delay p95 | ≤ 500 ms | 1189 / 1069 ms | 811 / 765 ms |
| First token | ≤ 2000 ms | 1284 / 1212 ms | 1324 / 1185 ms |
| Completion | ≤ 3000 ms | 1292 / 1218 ms | 1424 / 1221 ms |
| `POST /chat` p50 | — | 45 / 50 ms | 129 / 104 ms |
| Throughput | reported | 22.9 / 21.2 turns/s | 20.5 / 22.7 turns/s |

Fake remote model, p95 unless noted (two runs each):

| Metric | `main` | Concurrency 4 |
|---|---|---|
| Errors | 0 / 0 | 0 / 0 |
| Throughput | 0.99 / 1.03 turns/s | **3.78 / 4.06 turns/s** |
| Queue delay | 25.5 / 22.2 s | 5.2 / 4.0 s |
| First token | 26.8 / 23.5 s | 6.6 / 5.4 s |
| Completion | 27.3 / 24.0 s | 7.1 / 5.9 s |

## Reading
- With the model call as I/O, throughput rises about 4× (the concurrency) and every latency falls to about a
  quarter. Without it a worker serves one conversation for the whole model call, so two workers answer about one
  turn per 0.9 s whatever the load.
- With the mock engine all work is CPU on one host. Records start sooner (queue delay p95 down about a third), but
  first token, completion and throughput do not change, and `POST /chat` p50 rises because the workers now take CPU
  the gateways used. The queue-delay target still fails in both.
- These mock-engine figures for `main` are lower than those of 2026-10-29 (first token p95 2.2 s, queue delay p95
  2.0 s) on the same configuration; the host load differed between days. Only runs inside one report compare.
