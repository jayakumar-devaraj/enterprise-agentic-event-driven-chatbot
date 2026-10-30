# 0007 · Multi-layer guardrails and access-control binding

**Status:** Accepted 2026-10-17.

## Context
The system ingests untrusted user text, handles PII, and (with the approval gate) can take a
side-effecting action. The lead asked for multi-layer guardrails and basic auth. Access control must
bind an authenticated caller to their data; a filter on an unauthenticated, caller-supplied key does
not count.

## Decision
- **L1 Request:** HTTP Basic against `AUTH_USERS` (scrypt hashes, constant-time compare) → 401;
  `thread_owner:{thread_id}` set on first use to the authenticated user, any other user → 403 on
  chat, stream and approve; Pydantic validation → 422; size cap → 413.
- **L2 Abuse:** Redis fixed-window counter per thread → 429.
- **L3 PII:** Presidio scrub before Kafka; raw text never leaves the gateway process.
- **L4 Injection:** structural — fixed system prompt, user text only inside `<user_message>` with
  delimiters escaped; detection — contrastive fastembed similarity: top-1 against
  `injection_patterns` minus the nearer of `intents` and `benign_patterns`, cut `INJECTION_MARGIN`
  calibrated with leave-one-out (ADR 0004); a hit routes to a refusal node and is flagged in logs and
  spans, never silently dropped. The refund decision is a deterministic rule, not model output.
- **L5 Output:** Presidio scrub of model output per sentence chunk, `LLM_MAX_TOKENS` cap,
  system-prompt echo check and removal of echoed `<user_message>` / `<history>` blocks, before
  anything is checkpointed, streamed, logged or traced.
- llm-guard rejected (PyTorch, Python < 3.13).

## Consequences
- Ownership is bound to the authenticated principal, so guessing a `thread_id` yields 403, not data.
- Embedding-based injection detection is heuristic; structural separation is the primary defence.
- Basic credentials are protected on the wire only through the Caddy TLS entry point (:8443); the
  direct dev ports are plain HTTP (documented in known limitations).
- **Addendum 2026-10-29: accepted credentials are cached per process.** scrypt ran on every
  `POST /chat` and `GET /stream`, and was half of gateway CPU under load
  (`docs/reports/latency-2026-10-29-gateway-auth-cache.md`). `gateway.auth.Verifier` remembers a
  pair it has accepted, keyed by HMAC-SHA256 under a random per-process key, until the process
  exits; `AUTH_USERS` is read only at startup, so a changed password already needs a restart.
  Failures are never cached, so a guess still costs a full scrypt. Residual: a memory dump of a
  gateway holds the key with the tags, so guesses against a dumped tag cost an HMAC, not a scrypt;
  the same dump also holds Basic headers of requests in flight. Rejected: a TTL cache (it
  would add a clock to reason about without changing what a restart already guarantees).
