# 0006 · Approved deviations from the brief

**Status:** Accepted 2026-10-17 by the lead (`docs/design_dialogue.md` Rounds 2–4, Gate B).

## Context
The brief (`docs/prompts/problem-statement.md`) lists auth as a non-goal, fixes Jaeger as the trace
backend, has no side-effecting agent action, and gives an exact repo layout. The lead's chat
instructions take precedence over the brief and changed several of these.

## Decision
| Deviation | Brief said | Now | Approved in |
|---|---|---|---|
| Access control | Auth is a non-goal | HTTP Basic + thread ownership; adds 401/403 | Round 2 |
| LLM observability | OTel → Jaeger | OTel → Jaeger **plus** LangFuse Cloud US | Rounds 2, 4 |
| Human approval gate | — | Refund-request action gated by `interrupt()`; adds `POST /approve`, 404, `ApprovalRequiredEvent`, envelope fields `user_id`, `kind`, `ref_message_id`, `approved` | Rounds 2, 4 |
| Guardrails | Presidio + delimited prompt | Five layers incl. injection detection collection `injection_patterns` | Round 2 |
| Eval | — | DeepEval (dev-only) | Round 2 |
| Qdrant transport | REST :6333 | Worker searches over gRPC :6334 (`prefer_grpc`); REST kept for seed and healthcheck | Chat, 11:07 CDT |
| React client | Tier 2 | Restored after the Step B cut; deadline 17:40 CDT | Chat, 10:58 CDT |
| Deadline | Tier marks within 5 h | 7 h 20 min window ending 17:40 CDT, then extended to 19:30 and 20:30 CDT | Gate B, later go; "a", "b" after wrap-up |
| Cuts | Tiers 1–3 | Tier 3, A9, A10, service images/e2e cut at Step B (A8 restored); all rebuilt 17:07–19:20 (PRs #20–#25) except an automated A4 live-Qdrant-stop test | Step B; restored on the lead's request |

## Consequences
- Runtime is no longer fully offline: scrubbed text egresses to LangFuse Cloud.
- Basic auth is encrypted only through the Caddy TLS entry point (:8443); the direct dev ports send
  credentials in cleartext.
- `SPEC.md` carries the added contracts; anything not there is not guaranteed.
