# 0013 · Prometheus metrics and alert rules

**Status:** Accepted 2026-10-30.

## Context
Traces (Jaeger, LangFuse) and JSON logs explain one request; nothing measured the system over time, so a
stopped worker, a growing backlog or a burst of 5xx was noticed only by a user. The latency targets of
`docs/reports/latency-targets-2026-10-24.md` were checked by a load script, not continuously. The gateway's
API port is reachable through Caddy's `/api/*`, so anything served there is public.

## Decision
- The gateway and the worker expose Prometheus metrics with `prometheus-client` on `METRICS_PORT` (9100), a
  port of their own that compose never publishes; only the compose network reaches it.
- Gateway: `chatbot_gateway_requests_total{method,route,status}` and `chatbot_gateway_request_seconds`, from
  a pure ASGI middleware. `route` is the route template (`/stream/{thread_id}/{message_id}`) or `unmatched`,
  never the raw path, so thread and message ids cannot become labels. An exception no handler maps is
  counted as a 500. Latency ends when the response headers are sent, so an SSE stream counts until it opens.
- Worker: `chatbot_worker_records_total{outcome}` (`done`, `duplicate`, `dlq_malformed`, `dlq_dependency`),
  `chatbot_worker_record_seconds`, `chatbot_worker_queue_delay_seconds` (envelope `ts` to the worker owning
  the record), `chatbot_worker_in_flight` and `chatbot_worker_retries_total`.
- Consumer lag and DLQ growth are read from the broker by `kafka-exporter`, not from the workers: a worker
  that is down reports nothing, and its counters reset when it restarts.
- Prometheus (compose profile `ops`) discovers replicas through Docker DNS and evaluates
  `deploy/prometheus/alerts.yml`: no worker up, consumer lag, records dead-lettered, gateway 5xx ratio,
  `POST /chat` p95 and queue delay p95, the last two at the targets above. `promtool test rules` checks each
  rule against synthetic series in CI.
- No Alertmanager: where an alert goes (pager, chat, mail) is a deployment decision. Firing alerts show on
  the Prometheus alerts page.

## Consequences
- Each gateway and worker serves one more port; metric names and labels are now an interface for dashboards
  and rules, so renaming one means changing `alerts.yml` and its tests in the same change.
- Redis, Qdrant, the RAG service and the model server are not scraped; their faults show up indirectly, as
  5xx, retries, dead letters or latency.
- Histogram buckets bound how precisely a quantile can be read; the queue-delay and accept-latency targets
  fall on bucket boundaries (0.5 s, 1 s).
