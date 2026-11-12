# Real-model load, clean rerun, 2026-11-11

Judged against the addendum of 2026-11-11 ("a clean rerun of the real-model load") in
`latency-targets-2026-10-24.md`, committed and pushed (`924d4cb`) before the run. The targets are the
original real-model targets, unchanged.

## Method
As pre-registered:
- **Stack:** the `chatbot` compose project built from `main` (`1ce514f`), workers recreated with
  `MOCK_LLM=false` and `--profile llm` (Ollama `llama3.2:1b`, 4 CPU, 3 GB). 2 gateways, 2 workers,
  `WORKER_CONCURRENCY` 4.
- **LangFuse keys:** unset. A compose override drops the repo-root `.env`, and both workers were
  checked to have no LangFuse variables.
- **Other containers:** no other compose project. The standalone `jekyll-pages` container (about 2%
  CPU) was running.
- **Sequence:**
  1. Warm-up, discarded: 6 turns, 2 in flight, 22:50–22:52Z (`latency-2026-11-11-real-model-warmup.json`).
     Its first token p95 was 35.0 s and completion p95 37.5 s, which is the cold cost the
     2026-10-24 run paid inside its measurement.
  2. Measured, once: `uv run python tests/load/latency_a11.py --requests 20 --concurrency 2 --engine
     ollama`, 22:52–22:54Z (`latency-2026-11-11-real-model.json`).

This followed the five-minute mock-engine load runs (`latency-2026-11-11-backlog.md`) on the same
stack, with the workers recreated for the real model.

## Results
| Metric | Target | 2026-10-24 (confounded) | 2026-11-11 | Verdict |
|---|---|---:|---:|---|
| Errors | 0 of 20 | 0 | 0 | Pass |
| `POST /chat` p95 | ≤ 1 s | 704 ms | 127 ms | Pass |
| First token p95 | ≤ 10 s | 26.8 s | **10.78 s** | **Fail** |
| Completion p95 | ≤ 60 s | 63.4 s | 12.67 s | Pass |
| Queue delay p50 / p95 | reported | — / 32,960 ms | 17 / 34 ms | — |
| Empty replies | reported | 3 | 3 | — |
| Throughput | reported | 0.11 turns/s | 0.23 turns/s | — |

The first token p95 is taken over the 17 turns that produced a token. In those turns, the first token
came at 2.8–9.8 s, except for one turn at 14.7 s, which sets the p95. **The confounded run's
completion failure does not recur:** clean, completion p95 is 12.7 s. **The first-token target fails
by 0.78 s.**

## Empty replies
Turns 10, 11 and 17 ended with `done` and no token, after 6.6, 6.3 and 10.8 s. All 20 turns ask the
same question ("Why was I charged twice this month?"). This is the third run with 2–3 empty replies,
and the cause is still not investigated. A reply removed whole by an output guard would end this way,
but that is not established.
