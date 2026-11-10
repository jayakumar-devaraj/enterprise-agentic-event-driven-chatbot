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
- **Tests collected:** 254 unit, 64 integration (real Kafka, Redis, Qdrant, Postgres), 8 durability, 16 frontend.
- **Coverage:** 95 % of 1936 statements. Report header: "Coverage at 3ee1cc6 from CI run 42291685564 (unit + integration; scale, llm, durability and stack tests need special stacks)".
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
| Latency, mock engine, 200 turns at 75% of capacity (2026-11-03, two runs) | queue delay p95 55 and 105 ms; `POST /chat` p95 79–95 ms; first reply word p95 0.27–0.31 s; completion p95 0.33–0.36 s | queue ≤ 0.5 s | Pass |
| Latency, same runs, no growing backlog | queue delay p95 of the last 50 turns 3.6–4.7× the first 50, under 0.2 s | ≤ 2× | Fail as written |
| Latency, mock engine at saturation, 20 users with no pauses (same session) | queue delay p95 0.87–0.99 s; completion p95 1.2–1.5 s; 20.1–25.4 turns/s | reported | n/a |
| Latency, real model, 20 turns | accept request p95 0.7 s passes; full reply p95 63 s | ≤ 60 s | Fail (confounded run) |
| Throughput, real model on 4 CPUs, 16 turns per setting (2026-11-02) | 0.18–0.24 turns/s at every worker and model-server concurrency tried | reported | n/a |
| Reply quality, real model, 20 questions | mean 3.4 of 5; 16/20 without invented specifics | ≥ 3.5; ≥ 18/20 | Fail (scored by the build agent, not independent) |

**The most important trade-off.** Reliability over immediacy: the system answers "accepted" as soon
as the request is safely queued and delivers the reply through a queue and a replayable stream.
That removes timeouts, recovers from a crashed worker without repeating a reply or a refund, and
lets each part scale independently, at the cost of more moving parts (Kafka, Redis, Qdrant) than a
single web server. Data survives restarts; a whole-machine crash can lose up to the last second of
state (ADR 0008).

**Remaining work, in priority order.**
1. Latency: below capacity queue delay meets its target, but the pre-registered backlog check fails
   during a burst near capacity, and these 14-second runs cannot tell a burst from growth; a longer run
   would (`latency-2026-11-03-queue-delay.md`). With the real model the model server is the bottleneck:
   more worker concurrency only moves the wait into it (`latency-2026-11-02-real-model.md`).
2. Tenant security: the gateway accepts signed tokens from any OIDC issuer and the chat window signs in
   with PKCE, tested against a local Keycloak (ADR 0016); HTTP Basic remains when no issuer is set. Static
   tokens to the document service are now opt-in there, and the `rag` profile still opts in. Built from ADR
   0011: the document service validates signed tokens, the worker can use per-tenant client credentials
   (tested against a test double), and an optional exporter copies the audit stream into an append-only
   database. Not built: writing refund and access-denial events straight to that database, and any run
   against a production identity provider.
3. Detection quality: harmless off-topic questions are refused, and location names and some
   structured identifiers slip through the PII scrubber.
4. Reply quality and independent scoring: the local 1B model deflects most billing questions.
5. A deployment target (image registry for this repository's images, production hosting, certificates
   from a real CA in place of the development one) and closing the plain-HTTP development ports.
