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
