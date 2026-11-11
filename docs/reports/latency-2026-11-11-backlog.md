# Backlog check over five-minute open loops, 2026-11-11

Judged against the addendum of 2026-11-11 in `latency-targets-2026-10-24.md`, committed and pushed
(`9d2b504`) before any of these runs.

## Method
As pre-registered:
- **Code:** `main` at `1ce514f`, plus the per-tenth fields in `tests/load/latency_a11.py` (same commit).
- **Stack:** the `chatbot` compose project rebuilt from it (`up --build --scale gateway=2 --scale
  worker=2`): 2 gateways at 1 CPU, 2 workers at 2 CPU, `WORKER_CONCURRENCY` 4, `MOCK_LLM=true`.
- **LangFuse keys:** unset. A compose override dropped the repo-root `.env` (which holds only the
  LangFuse keys) from the gateway and worker, and both workers logged "LangFuse keys not set".
- **Other containers:** no other compose project ran. One standalone container, `jekyll-pages`
  (`ruby:3.3`, about 2% CPU, 75 MB), was running and was left alone.
- **Sequence:** a 20-turn warm-up (not judged), then closed loop → open loop → closed loop → open
  loop, 22:18–22:35Z.

Raw samples: `latency-2026-11-11-backlog-{closed1,open1,closed2,open2}.json`.

## Results
Milliseconds unless noted:

| Metric | Target | closed1 | open1 | closed2 | open2 |
|---|---|---:|---:|---:|---:|
| `X` / `R` (turns/s), turns | — | X 23.92, 200 | R 17.94, 5382 | X 20.42, 200 | R 15.32, 4596 |
| Wall time (s) | — | 8.4 | 304.5 | 9.8 | 303.0 |
| Errors | 0 | 0 | 0 | 0 | 0 |
| Traced turns | ≥ 90% of successes | 200 | **0** (see below) | 200 | 4596 |
| Queue delay p50 / p95 | p95 ≤ 500 (open loop) | 381 / 872 | — | 460 / 777 | 15 / **1459** |
| Queue delay p95, first tenth → last tenth | last ≤ 2 × first (open loop) | 458 → 938 | — | 496 → 720 | **55.3 → 116.7 (2.11×)** |
| `POST /chat` p95 | ≤ 1000 | 768 | 514 | 563 | 434 |
| First token p95 | ≤ 2000 | 1205 | **2086** | 1243 | **2182** |
| Completion p95 | ≤ 3000 | 1239 | 2182 | 1349 | 2316 |

Open loop 2, queue-delay p95 by tenth: 55, 1891, 3241, 50, 2042, 70, 32, 36, 39, 117 ms. The rate the
schedule offered in each tenth: 14.3–16.7 turns/s.

## Verdict
- **Open loop 1 is not judged.** None of its turns were traced, which is below the pre-registered 90%.
  The cause is the harness, not the stack: the script searched Jaeger with a depth of twice the turn
  count (10,764). Jaeger allows at most 10,000 and answered 400, and the script read that error body as
  "no traces". Its errors, `POST /chat` and completion pass, and its first token p95 (2086 ms) fails.
- **Open loop 2 fails:**
  - **Queue delay p95:** 1459 ms against ≤ 500.
  - **No growing backlog:** the last tenth is 2.11× the first, against ≤ 2×.
  - **First token p95:** 2182 ms against ≤ 2000.
  - **Passes:** errors, `POST /chat` and completion.
- **The 2026-11-03 targets fail.** They pass only if both open-loop runs pass.

## Reading
- **Stalls rather than growth.** In open loop 2, seven of the ten tenths have a p95 under 120 ms.
  Three tenths (2, 3 and 5) have a p95 of 1.9–3.2 s, and the following tenths return to tens of
  milliseconds. The arrival rate in those tenths was not unusual (14.3–16.7 turns/s across all ten).
  The record waits that long between the gateway's Kafka client and the worker's span, in episodes,
  and then the queue drains. The tenth rule fails narrowly (2.11×) on two quiet tenths, so the overall
  p95 is the more informative failure.
- **The old rule would have passed.** The 2026-11-03 rule (last 50 against first 50 turns) gives
  33 → 29 ms on open loop 2.
- **The 14-second runs missed this.** The 2026-11-03 runs (p95 55 and 105 ms) were too short to
  contain such an episode.
- **Run 1 shows the same pattern (informational, not a verdict).** Its traces were retrieved
  afterwards with the depth capped at 10,000 (`latency-2026-11-11-backlog-open1-retraced.json`): all
  5382 traced, p95 1428 ms, by tenth 331, 1481, 2728, 226, 275, 337, 428, 1350, 2214, 823 ms.
- **Not established:** what stalls the queue. No worker logged a transient failure or a Kafka
  rebalance during open loop 2, and Jaeger did not restart. Candidates to measure next, before any
  change: consumer fetch or commit pauses, Redis persistence (AOF rewrite), and the gateways' Kafka
  producer.

## Harness fix (after the runs; changes no figure above)
`queue_delays` now caps the Jaeger search depth at 10,000 and raises on an error response, so a lost
trace query fails loudly instead of reporting no traces.
