# Audit: problem-statement.md (per prompt_audit_template.md)

Rewrite: `problem-statement.v2.md` (not kept in this repository)

## 1. Structural Audit & Vulnerability Analysis

### Strengths
- Problem framed with concrete pains (504s, LLM-for-classification cost, PII leak) tied to design choices.
- Stack pinned with ports; $0/open-source boundary is crisp and testable.
- ASCII data-flow diagram numbers each hop (1-9), giving a checkable path.

### Vulnerabilities
1. **No role/persona, no task verb.** Nothing says what to produce, in what order, or when to stop.
2. **Return path undefined.** Hop 8 "pushes tokens back to gateway channel" names no mechanism (Kafka topic? Redis pub/sub or stream?).
3. **Internal contradictions.**
   - "Deterministic fallback mock" vs "No Placeholders" / "production-ready": no trigger, no rule that it be loud.
   - "HTTP/2 TLS 1.3" at the edge vs "local loopback Docker network": uvicorn does not serve HTTP/2; TLS termination point unspecified.
   - "Redis Cluster" on a single port; "Kafka managed via Confluent-Kafka" conflates broker with client library.
   - "$0 / no external endpoints" vs one-time model pulls (Ollama, HF, spaCy), unstated.
4. **Routing under-specified.** No seeded exemplars, metric, score threshold, or below-threshold behavior.
5. **"Redis thread memory state histograms"** is undefined; no schema, TTL, or checkpointer choice.
6. **PII scope vague.** No entity list, no spaCy model dependency, nothing on model output or logs.
7. **No prompt-injection defense** requirement for LLM calls.
8. **"Error safety handling wrappers"** invites blanket `except`; no 4xx/5xx split; no DLQ, retry, idempotency, partition key or consumer-group rules.
9. **No verification loop**: no tests, no fix-retry-escalate path, no ban on fabricated results.
10. **No file-boundary convention**, directory layout, or version pinning.
11. **No continuity mechanism, clarifying-question permission, or approval gate.**
12. **No observability or SLOs**; "real-time" unquantified.
13. Noise/typos: "React UI Chat Window Window", "smoothly".

## 2. Quantitative Scorecard
| Criterion | Score | Reason |
|---|---|---|
| Role Definition & Clarity | 2/10 | No role, no deliverable verb |
| Instruction Unambiguity | 5/10 | Strong stack list; contradictions, undefined return path and routing |
| Constraints & Edge Cases | 3/10 | No failure modes, tests, retry loop, fabrication ban, gating |
| Token Efficiency | 7/10 | Compact; minor decorative phrasing |
| **Overall** | **4.25/10** | |

## 3. Optimized Refusal & Boundary Protocol
See the "Refusals" section of `problem-statement.v2.md` (not kept in this repository).

## 4. Optimized Prompt (Version 2.0)
`problem-statement.v2.md` (not kept in this repository)

## 5. Patch cycle: V2.0 -> V2.1 (`problem-statement.v2.1.md` (not kept in this repository))

Residual gaps found in V2.0 and the patch that closes each:
| Gap in V2.0 | Patch in V2.1 |
|---|---|
| No time box or cut order for a 5-hour target | Tiers 1-3 with explicit cut order; elapsed time in each recap |
| "Tests" required but none specified | Seven named acceptance tests (PII, routing, streaming, failure, idempotency, memory, injection) |
| Message/SSE schemas undefined | Pydantic contracts for HTTP, Kafka envelope, token and terminal events |
| No SSE resume, disconnect, `thread_id` validation, rate limit, CORS | Specified |
| Redis checkpointer may need Redis modules | `VERIFY:` flag with decision rule |
| Langfuse self-hosted is heavy (multi-service) | Replaced by OpenTelemetry |
| Intent exemplars unsourced | >= 10 per intent in a committed seed file |
| No repo layout; compose startup ordering unstated | Layout fixed; healthchecks and `service_healthy` |
| Conflict with repo CLAUDE.md workflow | Explicit precedence line |
| Fallback-widening on failure not banned | Added to Refusals |

Score V2.1: Role 9, Unambiguity 9, Constraints 9.5, Token efficiency 8; overall about 9.1/10. Not 10:
- Never run against a model; the instruction-following claims are untested.
- Pinned-package behavior (Redis checkpointer, Presidio spaCy model, Kafka KRaft config) is unverified until built.
- The 5-hour estimate is unmeasured.
- Prompt is long (token cost) because it doubles as a spec.

## 6. Re-evaluation of V2.1 and patch to V2.2 (`problem-statement.v2.2.md` (not kept in this repository))

V2.1 re-scored 8.4/10 (Role 9, Unambiguity 8, Constraints 8.5, Token efficiency 8); the earlier 9.1 missed gap 1.

| Gap in V2.1 | Patch in V2.2 |
|---|---|
| Tier 1 required hops 1-9 tests but checkpointing, DLQ and memory test sat in Tier 2 | Checkpointing, DLQ, A1-A7 all in Tier 1; React, Ollama round trip, frontend test (A8, A9) in Tier 2 |
| Fenced-file delivery conflicted with repo commit/PR workflow | Write files with tools; show paths and diffs; commit and push per CLAUDE.md |
| Elapsed time could be fabricated | Measured via `date` at kickoff and each recap; added to Refusals |
| `message_id` origin and dedupe store unspecified | Client-generated UUID; Redis `SET NX` with TTL |
| Stream start id, trimming, rate-limit store unspecified | Read from id `0`; `MAXLEN` + TTL; Redis-backed limit |
| PII test covered only email and phone | A1 covers email, phone, card, SSN, IP |
| No frontend test | A8 |
| No toolchain line | `uv` and `npm`, lockfiles committed, versions queried |

Score V2.2: about 9.3/10. Not 10:
- Never run against a model; instruction-following is untested.
- Package behavior (Redis checkpointer modules, Presidio spaCy model, Kafka KRaft) is unverified until built.
- The 5-hour estimate is unmeasured.
- Size: the brief is long because it doubles as a spec.
