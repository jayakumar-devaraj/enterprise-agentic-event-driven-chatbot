# 0003 · confluent-kafka asyncio client; trace context in the envelope

**Status:** Accepted 2026-10-17 after spike (b).

## Context
The brief mandates `confluent-kafka` using its asyncio classes and asks whether the async path can
set per-message headers; if not, trace context travels only in the envelope's `trace_id`.

## Decision
- Producer and consumer use `confluent_kafka.aio.AIOProducer` / `AIOConsumer` (confluent-kafka
  2.16.0, librdkafka 2.16.0). `await producer.produce(...)` returns a delivery future that is awaited
  before the gateway answers 202; the consumer runs with `enable.auto.commit=false` and commits with
  `await consumer.commit(message=..., asynchronous=False)` after the durable outcome.
- **Headers:** `AIOProducer.produce(headers=...)` raises `NotImplementedError: Headers are not
  supported in AIOProducer batch mode`. Applying the pre-declared fallback, W3C trace context travels
  only in `Envelope.trace_id`, which carries the full W3C `traceparent` string; the worker
  continues the trace from it (`chat_common.tracing`, verified in `docs/reports/e2e-observability.md`).

Evidence (`tests/integration/test_spike_kafka_aio.py`, real Kafka 4.3.1):
```
test_aio_roundtrip_preserves_key_and_value PASSED
test_aio_producer_rejects_per_message_headers PASSED
```
The second test pins the limitation so an upgrade that adds header support is noticed.

## Consequences
- No reliance on Kafka headers anywhere; the envelope is the only carrier of trace context.
- Using the synchronous `Producer` for headers was rejected: it would put blocking calls on the
  gateway's event loop.

## Addendum 2026-10-24 · Unbatched producer with a delivery-report task
`AIOProducer`'s defaults (`batch_size=1000`, `buffer_timeout=1.0`) hold records in a Python buffer
until 1000 accumulate or 1 s passes with no new `produce()` call. Delivery futures resolve only on a
later `poll()`. Each `POST /chat` waits for its ack, so every request paid about 2 s, and under
sustained load sends timed out into 503s (`docs/reports/latency-2026-10-24.md`).

Decision: the gateway constructs `AIOProducer(..., batch_size=1, buffer_timeout=0)` and runs a task
that keeps awaiting `poll(0.1)` until close. Each record goes to librdkafka at once, which batches
on its own `linger.ms`, and acks resolve within about a poll interval. The client stays the
mandated asyncio class. The synchronous `Producer` with a report thread performed the same in
isolation (p50 ~15 ms), but it would have departed from this ADR's mandate for no gain.

Consequences: a send's latency is the broker round trip (p50 ~15 ms in isolation, steady-state POST
p95 181 ms under the mock load). The report task occupies one thread of the producer's executor.
The worker's DLQ producer keeps the defaults: it is off the request path, and its awaits are bounded
by the worker's own outcome handling.
