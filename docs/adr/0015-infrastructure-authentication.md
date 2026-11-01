# 0015 · Authentication on the data stores

**Status:** Accepted 2026-11-01 (Redis); Qdrant, Kafka and TLS follow as addenda.

## Context
Redis, Kafka and Qdrant accepted any client that could reach them: on the compose network, and on the
host ports bound to 127.0.0.1. Redis holds every stream, checkpoint, dedupe lease, owner binding, refund
and the audit trail, so any local process could read conversations, forge a refund marker or rewrite
history (`docs/known_limitations.md`, "Loopback is the trust boundary").

## Decision
- Redis runs with the passwordless `default` user off and one ACL user, `chatbot`, whose password is
  `REDIS_PASSWORD`. Every client authenticates through `REDIS_URL`
  (`redis://chatbot:<password>@redis:6379/0`), which compose builds for the gateway, the worker and the
  audit exporter. The host port requires the same credentials.
- No password is committed. Compose refuses to start without `REDIS_PASSWORD` (`${VAR:?}`), the way it
  is given `AUTH_USERS`; CI generates a random one per job; host-side tests read it from the environment.
- One user for all services: they all need the same keys today, and per-service users with key patterns
  would only restate that. Splitting it is the change to make when a service needs less.

## Consequences
- An existing local stack must be started with `REDIS_PASSWORD` exported; its data volumes keep working
  (ACLs are not stored in the AOF), and the same password must be used again for the services to connect.
- The password appears in the container's command line and environment (`docker inspect`), so anyone
  with Docker access on the host still reads it; an ACL file from a secrets store is the step beyond.
- Traffic is still plaintext until the TLS addendum.
