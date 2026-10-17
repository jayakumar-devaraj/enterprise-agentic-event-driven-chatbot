# Functional verification — end-to-end run with traces (Task 11, rung 3)

Date: 2026-10-17, ~14:35 CDT. Stack: `docker compose -f deploy/compose.yaml up -d --wait` (Kafka,
Redis, Qdrant, Jaeger). Processes started from the repo root with host endpoints
(`KAFKA_BOOTSTRAP_SERVERS=127.0.0.1:19092`, `REDIS_URL=redis://127.0.0.1:16379/0`,
`QDRANT_URL=http://127.0.0.1:16333`, `QDRANT_GRPC_PORT=16334`,
`OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:14317`, `MOCK_LLM=true` default, LangFuse keys from the
local, git-ignored `.env`, a demo user generated with `python -m gateway.auth`):

```
uv run python -m worker.main
uv run uvicorn gateway.app:create_app --factory --port 18000
```

## Observed

`POST /chat` (Basic auth, message "My name is John Smith and I was charged twice"):
```
{"message_id":"885d76db-f400-40a8-99f8-c06f31dad869","thread_id":"e2e-14739","stream_url":"/stream/e2e-14739/885d76db-f400-40a8-99f8-c06f31dad869"}
```
`GET /stream/e2e-14739/885d76db-…` (curl -N):
```
event: token  data: {"type":"token","seq":0,"token":"[mock]"}          id: 1792266851186-0
event: token  data: {"type":"token","seq":1,"token":"intent=billing"}  id: 1792266851201-0
event: token  data: {"type":"token","seq":2,"token":"turns=1"}         id: 1792266851213-0
event: token  data: {"type":"token","seq":3,"token":"last=-"}          id: 1792266851220-0
event: done   data: {"type":"done","seq":4}                            id: 1792266851231-0
```
Jaeger v2 query (`/api/v3/traces?query.service_name=worker…`), one trace across both services:
```
88752d76c5e247880d7eec5212609922 [('gateway', 'POST /chat'), ('gateway', 'POST /chat http receive'), ('gateway', 'POST /chat http send'), ('worker', 'respond'), ('worker', 'route'), ('worker', 'worker.process')]
```
LangFuse Cloud US (`/api/public/v2/observations`), same trace id:
```
[('respond.billing', 'GENERATION', None, 'None', '88752d76c5e247880d7eec5212609922')]
```

## Notes
- Routing used the real Qdrant over gRPC (intent `billing`); the reply came from the mock engine.
- The v2 observations listing does not return `model`/`output` by default, so those show `None`
  above; the observation's presence and trace linkage are what was verified.
- Jaeger v2 serves the v3 query API; the legacy `/api/traces?service=` search returns 404, while
  `/api/traces/{trace_id}` works (used by `tests/integration/test_tracing_jaeger.py`).
