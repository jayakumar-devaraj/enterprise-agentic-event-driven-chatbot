# Summary for leadership

**What was built.** A customer-support chatbot backend that stays responsive under load and keeps
personal data out of its internals. A message is accepted instantly, cleaned of personal data
(names, emails, phone, card, SSN, IP, places) before it enters the system, routed to billing,
technical support or general help by a local model at no per-message cost, answered by a local
language model (or a deterministic stand-in for testing), and streamed back word by word to a web
chat window. Refunds are never issued automatically: the customer must approve each one. Every
request is traceable end to end (Jaeger locally, LangFuse Cloud for AI-level traces), and key
actions are written to a durable audit log.

**Evidence.** 67 unit tests, 19 integration tests against the real message broker, database and
vector store, and 5 front-end tests, all passing locally and in GitHub Actions; plus a scale test
(2 gateways + 2 workers), a real local-LLM round trip, and HTTPS through a reverse proxy. 88 % code
coverage (`docs/reports/coverage.txt`). The whole stack starts with one command. Measured PII recall 100 % on a small, easy
32-sentence sample; this overstates real-world accuracy.

**The most important trade-off.** Reliability over immediacy: the system answers "accepted" at once
and delivers the reply through a queue and a replayable stream. That removes timeouts, survives
crashes (work is retried or reprocessed, never lost or duplicated) and lets each part scale
independently, at the cost of more moving parts (Kafka, Redis, Qdrant) than a single web server.

**Remaining work, in priority order.**
1. Set latency targets: under 20 concurrent users the first word of a reply takes about 4.4 s
   (median) with the test engine. Measured cause: the workers run at their CPU limit, and the
   message-queue client holds each request for up to 1 s; the next experiments are listed, no fix is
   shipped yet.
2. Improve the injection detector (one legitimate refund question was flagged) and test PII
   detection on harder, real-world text before setting an accuracy target.
3. Re-check reply quality with the local model (an echo of the user's question is now filtered,
   not yet re-tested against the model).
4. Add a deployment target (image registry, production hosting) and close the remaining plain-HTTP
   development ports.
