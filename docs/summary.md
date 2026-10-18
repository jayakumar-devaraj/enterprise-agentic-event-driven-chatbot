# Summary for leadership

**What was built.** A customer-support chatbot backend that stays responsive under load and keeps
personal data out of its internals. A message is cleaned of personal data (names, emails, phone,
card, SSN, IP, places) before it enters the system, accepted once it is safely queued, routed to
billing, technical support or general help by a local model at no per-message cost, answered by a
local language model (or a deterministic stand-in for testing), and streamed back word by word to a
web chat window. Refunds are never issued automatically: the customer must approve each one, and an
approved refund is recorded as a request for a person to process (no payment system is connected).
Every request is traceable end to end (Jaeger locally, LangFuse Cloud for AI-level traces), and key
actions are written to a durable audit log.

**Evidence.** 78 unit tests, 34 integration tests against the real message broker, database and
vector store, 3 durability tests (kill, container replacement, backup restore) and 10 front-end
tests, run on every change in GitHub Actions; plus an earlier scale test (2 gateways + 2 workers),
a real local-LLM round trip, and HTTPS through a reverse proxy. Crash recovery is tested by
injecting failures at each step in-process, not yet by killing live containers. Measured PII recall
is 100 % on a small, easy 32-sentence sample, which overstates real-world accuracy. The committed
coverage report (88 %) predates the 2026-10-17 audit fixes.

**The most important trade-off.** Reliability over immediacy: the system answers "accepted" as soon
as the request is safely queued and delivers the reply through a queue and a replayable stream.
That removes timeouts, recovers from a crashed worker without repeating a reply or a refund, and
lets each part scale independently, at the cost of more moving parts (Kafka, Redis, Qdrant) than a
single web server. Data now survives restarts; a whole-machine crash can lose up to the last second
of state (ADR 0008).

**Remaining work, in priority order.**
1. Set latency targets: under 20 concurrent users the first word of a reply takes about 4.4 s
   (median) with the test engine. Measured cause: the workers run at their CPU limit, and the
   message-queue client holds each request for up to 1 s; the next experiments are listed, no fix is
   shipped yet.
2. The prompt-injection detector now catches 20 of 20 held-out attacks, but still refuses 4 of 20
   legitimate held-out messages; test PII detection on harder, real-world text before setting an
   accuracy target.
3. Measure reply quality: the automated evaluation is built but has not completed a run, because
   the local model is too slow on CPU to act as its own judge.
4. Add a deployment target (image registry, production hosting) and close the remaining plain-HTTP
   development ports.
