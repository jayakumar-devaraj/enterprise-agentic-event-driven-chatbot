# Queue delay under an open-loop load, 2026-11-03

Judged against the addendum of 2026-11-03 in `latency-targets-2026-10-24.md`, committed and pushed
(`728e54e`) before any of these runs. Nothing below changed the method.

## Method
As pre-registered. Current `main` (`7a430c0`) plus `tests/load/latency_a11.py --rate`, a compose project of
its own (`chatbot-qd`): 2 gateways at 1 CPU, 2 workers at 2 CPU, `WORKER_CONCURRENCY` 4, `MOCK_LLM=true`, no
LangFuse keys, no other compose project running. A 20-turn warm-up, then closed loop (200 turns, 20 in
flight) → open loop at `R = 0.75 × X` (200 turns, exponential gaps, seed 0) → closed loop → open loop.
Raw samples: `latency-2026-11-03-queue-delay-{closed1,open1,closed2,open2}.json`.

## Results
Milliseconds unless noted:

| Metric | Target | closed1 | open1 | closed2 | open2 |
|---|---|---|---|---|---|
| Throughput `X` / arrival rate `R` (turns/s) | — | X = 20.12 | R = 15.09 | X = 25.41 | R = 19.06 |
| Errors | 0 | 0 | 0 | 0 | 0 |
| Queue delay p50 | — | 429 | 14 | 378 | 19 |
| **Queue delay p95** | ≤ 500 (open loop) | 985 | **55** | 874 | **105** |
| Queue delay p95, first 50 → last 50 | last ≤ 2 × first (open loop) | 1053 → 969 | **28 → 131** | 769 → 653 | **51 → 183** |
| `POST /chat` p95 | ≤ 1000 | 665 | 79 | 424 | 95 |
| First token p95 | ≤ 2000 | 1351 | 274 | 1179 | 311 |
| Completion p95 | ≤ 3000 | 1494 | 334 | 1213 | 355 |

## Verdict
- **Queue delay p95 ≤ 500 ms: passes** in both open-loop runs (55 and 105 ms).
- **No growing backlog: fails as written** in both: the last 50 traced turns' p95 is 4.7× and 3.6× that of the
  first 50 (the criterion allows 2×), although it stays under 200 ms.
- Errors, `POST /chat`, first token and completion pass in all four runs.
- At saturation (closed loop) queue delay p95 is 874–985 ms, as on 2026-10-30: reported, not judged.

## Reading
- Below capacity, a record waits about 15–20 ms (p50) between the gateway's Kafka client and the worker. The
  ~400 ms p50 of the closed loop is waiting for one of the 8 worker slots, as the addendum's arithmetic predicted.
- Seed 0's schedule is not even over the run: its first 50 turns arrive at 12.8 and 16.2 turns/s (about 64% of
  `X`), its last 50 at 18.6 and 23.5 turns/s (about 92%). The later delays follow that burst. A 14-second run
  cannot separate this from a backlog that grows with time; a longer run, pre-registered the same way, would.
- Capacity differed by a quarter between the two closed runs (20.1 and 25.4 turns/s) on the same stack minutes
  apart, so only runs paired within one session compare.
