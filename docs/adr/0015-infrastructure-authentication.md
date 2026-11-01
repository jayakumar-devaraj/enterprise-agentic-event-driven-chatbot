# 0015 · Authentication on the data stores

**Status:** Accepted 2026-11-01 (Redis, Qdrant, Kafka, TLS).

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
- Traffic was plaintext until the TLS addendum below.

## Addendum 2026-11-01: Qdrant
Qdrant runs with `QDRANT__SERVICE__API_KEY` set from `QDRANT_API_KEY`, which then guards every REST and
gRPC call. The worker and the seed pass the same key (`Settings.qdrant_api_key`); compose refuses to start
without it, CI generates one per job and host-side tests read it from the environment. Until TLS the key
travels in clear, and qdrant-client warns about it.

## Addendum 2026-11-01: Kafka
Every listener (the broker's `INTERNAL`, the host's `EXTERNAL` and the `CONTROLLER`) is
`SASL_PLAINTEXT` with mechanism PLAIN and one user, `chatbot`, whose password is `KAFKA_PASSWORD`. PLAIN
rather than SCRAM: KRaft creates SCRAM users only at storage format or through the controller, which
would need an unauthenticated bootstrap path; PLAIN's weakness, the password in clear, is what TLS
removes. Every client starts from `chat_common.kafka.client_config`; CLI tools in the containers run
through `deploy/kafka/cli.sh`, and the lag exporter authenticates with the same user. No ACL
authorizer: there is one principal, and every service needs what it can do.

## Addendum 2026-11-01: TLS
Redis (TLS port only), every Kafka listener (`SASL_SSL`) and Qdrant (REST and gRPC) now encrypt traffic,
so the passwords and the API key above no longer cross the network in clear. A one-shot `certs` service
writes a development CA and one server certificate (SANs `redis`, `kafka`, `qdrant`, `localhost`,
`127.0.0.1`) to `deploy/certs/` on first start and reuses them after; every client verifies the server
against that CA (`INFRA_CA_FILE`, `ssl_ca_certs` in `REDIS_URL`, `GRPC_DEFAULT_SSL_ROOTS_FILE_PATH` for
Qdrant's gRPC). Clients do not present certificates: identity stays the password or key.
Consequences: the CA and server key are development material, readable by the containers and anyone with
the checkout; a deployment replaces `deploy/certs/` with certificates from its own CA and keeps the same
paths. The certificate lasts 825 days and is not renewed automatically.
