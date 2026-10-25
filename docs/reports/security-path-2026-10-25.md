# HTTPS path, exposed ports and credentials, 2026-10-25

Checked on the running development stack (`main` code, 2 gateways, 2 workers, Caddy). Commands
and outputs are summarised below. Claims are scoped to the threat model at the end.

## HTTPS user path (Caddy, `https://localhost:8443/api/...`)
| Check | Observed |
|---|---|
| TLS | TLS 1.3, `TLS_AES_128_GCM_SHA256`, certificate from `Caddy Local Authority - ECC Intermediate` (not publicly trusted) |
| HTTP/2 | ALPN `h2` when offered (`openssl s_client -alpn h2,http/1.1`). The local curl (Schannel build) cannot do HTTP/2 and fell back to 1.1 |
| `GET /healthz` | 200 |
| `POST /chat` without credentials | 401 |
| `POST /chat` with a wrong password | 401 |
| `POST /chat` with `demo` credentials | 202, then the SSE stream ended with `event: done` |

## Published ports
Every published port binds to `127.0.0.1` (`docker ps`, `deploy/compose.yaml`):

| Port | Service | Protocol / auth |
|---|---|---|
| 8443 | Caddy | HTTPS, Basic auth at the gateway |
| 8002, 8003 (range 8000–8003) | gateways | **plain HTTP**, Basic auth: credentials in clear on loopback |
| 5173 | frontend (Vite) | plain HTTP |
| 19092 | Kafka | plaintext, **no auth** |
| 16379 | Redis | **no auth** (`redis-cli -p 16379 ping` → `PONG`) |
| 16333 / 16334 | Qdrant | **no auth** |
| 16686, 14317, 14318 | Jaeger UI and OTLP | **no auth** |
| 11435 | Ollama | **no auth** |

Workers publish no ports.

## Credentials
- Passwords are stored only as scrypt hashes in `AUTH_USERS`, from the environment. Verification
  is constant-time and runs off the event loop (ADR 0007).
- `.env` is git-ignored and untracked. The only `AUTH_USERS` hash-shaped value in tracked files is
  the `.env.example` placeholder `REPLACE_ME_user:scrypt-hash`, which a unit test requires to be
  rejected.
- Container logs from the last 24 h (6171 lines, including authenticated load and evaluation runs)
  contain no `demo-pw`, no `Authorization` header, no base64 `demo` credentials and no scrypt hash.
- Every request in the 2026-10-24 latency runs and the 2026-10-25 safety run was authenticated
  (`docs/reports/latency-2026-10-24.md`).
- Raw PII in logs, Kafka, the DLQ, Redis and traces: see `tests/stack/test_sensitive_data.py` and
  `docs/known_limitations.md`.

## Threat model these results support
A **single-host development deployment whose trust boundary is the loopback interface.** Against a
network attacker, only loopback ports are published, and the user path through Caddy is TLS 1.3
with HTTP/2. Against **any local process**, the infrastructure is open: Redis, Kafka, Qdrant,
Jaeger and Ollama have no authentication, so a local process could read checkpoints, streams,
refund records and audit entries, or write them. The direct gateway ports also carry Basic
credentials in clear. A deployment beyond one trusted host needs authentication and TLS on every
infrastructure service, a network where only Caddy is published, and a publicly trusted
certificate. None of that is implemented or claimed here.
