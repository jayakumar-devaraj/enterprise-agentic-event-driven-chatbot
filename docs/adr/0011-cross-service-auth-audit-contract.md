# 0011 · Cross-service auth, audit and contract with the RAG service

Status: **Accepted (Round 2 of 2, 2026-10-28).** Round 1 below is kept as written; the final
decision is "Decision (Round 2)".
The matching record in the RAG repository is its ADR 0002 (same title).

## Context

Document questions are answered by a separate RAG service (ADR 0010). Four things cross the boundary
and are decided today in each repository on its own:

- **Authentication.** The worker sends `Authorization: Bearer <token>` built from `RAG_TENANT_TOKENS`,
  and the gateway maps users to tenants with `USER_TENANTS`. The RAG service resolves the tenant from
  a static `TENANT_TOKENS` list. There is no expiry, rotation or scope.
- **Audit.** This repository writes events to the Redis stream `audit:events`, trimmed at about
  100,000 entries (`chat_common/audit.py`), with `recent()` as the only query. Redis AOF `everysec`
  can lose about a second (ADR 0008). The RAG service keeps JSON logs only.
- **Contract.** `docs/contracts/rag-openapi-v1.json` here and `docs/openapi-v1.json` there were
  byte-identical on 2026-10-28, but nothing in CI keeps them so.
- **Release pinning.** `RAG_API_IMAGE` defaults to `ghcr.io/jayakumar-devaraj/multi-tenant-agentic-rag:v0.1.0`,
  a mutable tag.

## Decision (Round 1)

Decided by the lead in chat on 2026-10-28:

1. **Auth is OIDC/JWT validated against a JWKS.** Tokens are short-lived and carry a tenant claim and
   scopes (`rag:query`, `rag:ingest`). Rotation comes from the identity provider. A local identity
   provider runs in compose for development and tests.
2. **The durable audit trail is an append-only Postgres table.** The application role has `INSERT`
   and `SELECT` only. Redis `audit:events` stays as a fast buffer, not the record.

Proposed for critique:

3. **Gateway to worker to RAG propagates identity, not a shared secret.** The worker never holds a
   token that can name an arbitrary tenant. See open question A.
4. **Request correlation.** The `X-Request-ID` and `traceparent` already sent to the RAG service are
   written into both audit tables, so one request can be followed across both services.
5. **Contract check in CI.** A job in each repository fetches the other's contract file at its pinned
   release and fails on any difference.
6. **Release pinning by digest.** `RAG_API_IMAGE` is pinned as `name@sha256:<digest>`; the tag is kept
   in a comment. Moving the pin is a reviewed change.
7. **Audit export.** A consumer group reads `audit:events` and appends to the Postgres table with an
   idempotency key (stream entry id), so replays do not duplicate rows.

## Open questions for Round 2

- **A. How does the worker get a tenant-scoped token?** Options: per-tenant client credentials held
  by the worker (simple; N secrets); OAuth 2.0 token exchange (RFC 8693) where the worker exchanges
  a gateway-issued user assertion (no standing tenant secret; needs IdP support); one service
  principal plus a signed tenant assertion (smaller IdP surface; the RAG service must trust the
  worker's assertion key). The lead's preference is needed before implementation.
- **B. Does the chatbot's gateway keep HTTP Basic for end users?** This record does not change it.
  A separate ADR would be needed to move users to OIDC.
- **C. Audit retention period and recovery-point objective.** Needed to size the table and the
  exporter lag alarm.

## Decision (Round 2)

The lead reviewed Round 1 and the agent's recommendations in chat on 2026-10-28 and agreed to them.
Points 1–2 of Round 1 stand. Points 3–7 are accepted, with these answers to the open questions:

- **A. Token shape: per-tenant client credentials now; token exchange later.** The worker holds one
  OIDC client per tenant, from a secrets manager, never from environment files. The identity provider
  fixes each client's `tenant` claim and `rag:query` scope, so a leaked secret exposes one tenant and
  the worker cannot name an arbitrary one. This supersedes the "token exchange" lean in Round 1:
  exchange needs a user token as the subject, and the gateway authenticates users with HTTP Basic, so
  it has none to exchange. Token exchange (RFC 8693) is adopted when users move to OIDC (B); the
  chosen identity provider must be confirmed to support it first.
- **B. The gateway keeps HTTP Basic for end users.** This is a known gap, not an endorsement. A
  separate ADR moves the gateway to OIDC.
- **C. Audit tiers.** Events that must not be lost (refund proposed, approved, applied or rejected;
  authentication failures; cross-tenant denials) are written synchronously to Postgres. All other
  events go through the exporter, with an alarm when its lag exceeds 60 seconds. Default retention is
  400 days queryable, partitioned by month, with older partitions archived. This default is not a
  compliance determination and is replaced by the real requirement when it is confirmed.
- **Rollout order:** RAG repository first (migrations, role split, audit table, row-level security;
  then JWT validation accepting both token types), then this repository (client credentials, digest
  pin, contract check, audit writes and exporter), then removal of static tokens in both.


- A new Postgres dependency is added to this stack for the audit table (the first one here).
- Identity-provider availability becomes a dependency of document questions. Behaviour when the
  provider or JWKS is unreachable must fail closed with an `ErrorEvent` and a DLQ record, never fall
  back to a static token (CLAUDE.md §9 invariant on silent degradation).
- `RAG_TENANT_TOKENS` and `USER_TENANTS` are superseded once question A is settled; until then they
  remain the only mechanism and this record changes no running behaviour.
- Implementation is split into separate pull requests per decision after Round 2.

## Amendment (2026-10-29, client credentials in the worker)

Decision A is implemented in `services/worker/worker/rag_auth.py`. With `RAG_TOKEN_URL` and
`RAG_TENANT_CLIENTS` (`tenant:client_id:secret,...`) set, the worker obtains one access token per tenant
with the OAuth 2.0 client-credentials grant, caches it in memory until 30 s before it expires, and lets
one caller per tenant fetch at a time. A refused client (4xx) is a final `rag_unauthorized`; an
unreachable or failing token endpoint (5xx, transport error) is retryable like any other dependency
fault; neither is answered with a static token. Settings fail startup if both `RAG_TENANT_TOKENS` and
`RAG_TENANT_CLIENTS` are set, or if the clients have no URL. A 403 from the RAG service (a token without
the `rag:query` scope) maps to the same `rag_unauthorized` as a 401. Verified at the HTTP boundary with a
test double for the token endpoint; no real identity provider (for example Keycloak) has been run.
Still open from this record: the audit store and exporter, the digest pin, and the cross-repository
contract check.
