# Summary for leadership

**What was built.** A customer-support chatbot backend that stays responsive under load and keeps
personal data out of its internals. A message is accepted instantly, cleaned of personal data
(names, emails, phone, card, SSN, IP, places) before it enters the system, routed to billing,
technical support or general help by a local model at no per-message cost, answered by a local
language model (or a deterministic stand-in for testing), and streamed back word by word to a web
chat window. Refunds are never issued automatically: the customer must approve each one. Every
request is traceable end to end (Jaeger locally, LangFuse Cloud for AI-level traces), and key
actions are written to a durable audit log.

**Evidence.** 66 unit tests, 18 integration tests against the real message broker, database and
vector store, and 5 front-end tests, all passing locally and in GitHub Actions (CI run 39065303808);
87 % code coverage (`docs/reports/coverage.txt`). Measured PII recall 100 % on a small, easy
32-sentence sample; this overstates real-world accuracy.

**The most important trade-off.** Reliability over immediacy: the system answers "accepted" at once
and delivers the reply through a queue and a replayable stream. That removes timeouts, survives
crashes (work is retried or reprocessed, never lost or duplicated) and lets each part scale
independently, at the cost of more moving parts (Kafka, Redis, Qdrant) than a single web server.

**Remaining work, in priority order.**
1. Package the gateway, worker and seed as container images and run the full stack in one command
   (today they run as local processes).
2. Verify one real round trip through the local language model (built, not yet tested), and measure
   latency under load.
3. Prove horizontal scaling (two gateways and two workers) with an automated test.
4. Add HTTPS: passwords currently travel unencrypted on the local network.
5. Improve the injection detector (one legitimate refund question was flagged) and test PII
   detection on harder, real-world text before setting an accuracy target.
