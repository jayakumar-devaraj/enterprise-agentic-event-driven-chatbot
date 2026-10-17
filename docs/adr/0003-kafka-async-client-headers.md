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
  only in `Envelope.trace_id`; the worker rebuilds the parent span context from it (Task 11).

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
