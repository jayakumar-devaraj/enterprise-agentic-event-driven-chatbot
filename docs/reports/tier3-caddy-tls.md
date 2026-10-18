# Tier 3 — Caddy TLS / HTTP/2 reverse proxy

2026-10-17 ~19:06 CDT, `caddy:2.11.7`, `deploy/caddy/Caddyfile` (internal CA, `localhost`),
host port 8443. Probed with httpx + h2 (one-off `uv run --with h2`, not a project dependency):
```
healthz 200 HTTP/2 {"status":"ok"}
ui 200 HTTP/2 True
chat 202 HTTP/2
stream HTTP/2 25 events, last {'type': 'done', 'seq': 24}      # real Ollama reply (MOCK_LLM=false)
```
`openssl s_client -servername localhost -alpn h2`:
```
issuer=CN=Caddy Local Authority - ECC Intermediate
ALPN protocol: h2
```
Plain HTTP to the TLS port is refused (`400`). `/api/*` is load-balanced across gateway replicas
via Docker DNS (`dynamic a gateway 8000`), with `flush_interval -1` so SSE is not buffered.
