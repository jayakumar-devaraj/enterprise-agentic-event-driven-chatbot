# Summary for leadership

**What was built.** A customer-support chatbot backend that stays responsive under load and keeps
personal data out of its internals. A message is cleaned of personal data before it enters the
system, accepted once it is safely queued, routed to billing, technical support, general help or
company-document questions by a local model at no per-message cost, answered by a local language
model (or a deterministic stand-in for testing), and streamed back word by word to a web chat
window. Document questions are answered by a separate retrieval service, per customer organisation
(tenant). Refunds are never issued automatically: the customer must approve each one, and an approved
refund is recorded as a request for a person to process (no payment system is connected). Every
request is traceable end to end, and key actions are written to an audit log.

**Evidence.** Figures below are generated from the repository by `scripts/gen_figures.py`; CI fails
when they drift (`docs/reports/figures.json`).

<!-- figures:start -->
- **Tests collected:** 213 unit, 56 integration (real Kafka, Redis, Qdrant, Postgres), 8 durability, 11 frontend.
- **Coverage:** 89 % of 1606 statements. Report header: "Coverage at efe4fea from CI run 40929094525 (unit + integration; scale, llm, durability and stack tests need special stacks)".
<!-- figures:end -->

Crash recovery is tested by injecting failures at each step, by stopping Qdrant live, and by killing the
live worker container under load. The measured results below come from pre-registered reports in
`docs/reports/`.

| Area | Result | Target | Verdict |
|---|---|---|---|
| Safety (end to end, real model) | 0 refunds applied, 0 prompt leaks, 0/16 canary leaks | 0 | Pass |
| Safety, off-topic questions wrongly refused | 4 of 10 | ≤ 1 | Fail |
| PII, structured identifiers redacted | 10/14 = 0.71 | ≥ 0.90 | Fail |
| PII, locations redacted | 16/30 = 0.53 | ≥ 0.80 | Fail |
| Latency, mock engine, 200 turns, worker concurrency (2026-10-30, two runs) | `POST /chat` p95 0.6–0.8 s; first reply word p95 1.2–1.3 s; completion p95 1.2–1.4 s; queue delay p95 0.77–0.81 s; 20.5–22.7 turns/s | queue ≤ 0.5 s | Fail |
| Latency, real model, 20 turns | accept request p95 0.7 s passes; full reply p95 63 s | ≤ 60 s | Fail (confounded run) |
| Reply quality, real model, 20 questions | mean 3.4 of 5; 16/20 without invented specifics | ≥ 3.5; ≥ 18/20 | Fail (scored by the build agent, not independent) |

**The most important trade-off.** Reliability over immediacy: the system answers "accepted" as soon
as the request is safely queued and delivers the reply through a queue and a replayable stream.
That removes timeouts, recovers from a crashed worker without repeating a reply or a refund, and
lets each part scale independently, at the cost of more moving parts (Kafka, Redis, Qdrant) than a
single web server. Data survives restarts; a whole-machine crash can lose up to the last second of
state (ADR 0008).

**Remaining work, in priority order.**
1. Latency: on the mock engine only queue delay still fails (p95 0.77–0.81 s against 0.5 s). Workers
   now run 4 records at once, one per thread (ADR 0012): against a model that takes about 1.8 s per reply,
   throughput rose about 4× (`latency-2026-10-30-worker-concurrency.md`). Not yet measured with a real
   model server.
2. Tenant security: end users still use HTTP Basic, and static tokens remain the default for calls to
   the document service. ADR 0011 (accepted) moves tenant authentication to short-lived signed
   tokens and the audit trail to a durable store. Built: the document service validates signed
   tokens, the worker can use per-tenant client credentials, and an optional exporter copies the
   audit stream into an append-only database. Not built: writing refund and access-denial events
   straight to that database, and any run against a real identity provider.
3. Detection quality: harmless off-topic questions are refused, and location names and some
   structured identifiers slip through the PII scrubber.
4. Reply quality and independent scoring: the local 1B model deflects most billing questions.
5. A deployment target (image registry, production hosting) and closing the plain-HTTP development
   ports.
