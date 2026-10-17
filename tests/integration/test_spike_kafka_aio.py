"""Spike (b): confluent-kafka asyncio producer/consumer import path and per-message headers.

Outcome on confluent-kafka 2.16.0: `confluent_kafka.aio.AIOProducer` / `AIOConsumer` work for
key/value round trips, but `AIOProducer.produce(headers=...)` raises NotImplementedError. Per the
pre-declared fallback (ADR 0003), trace context travels only in the envelope's `trace_id`. The
second test pins that behaviour so an upgrade that adds header support is noticed.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka.aio import AIOConsumer, AIOProducer

pytestmark = pytest.mark.integration


@pytest.fixture
async def topic(kafka_bootstrap: str) -> AsyncIterator[str]:
    """A throwaway single-partition topic."""
    name = f"spike-{uuid.uuid4().hex[:8]}"
    admin = AdminClient({"bootstrap.servers": kafka_bootstrap})
    admin.create_topics([NewTopic(name, num_partitions=1, replication_factor=1)])[name].result(15)
    yield name
    admin.delete_topics([name])


async def test_aio_roundtrip_preserves_key_and_value(kafka_bootstrap: str, topic: str) -> None:
    """Async produce, then async consume with a manual commit, returns the same key and value."""
    producer = AIOProducer({"bootstrap.servers": kafka_bootstrap})
    delivered = await producer.produce(topic, value=b'{"x":1}', key=b"thread-1")
    await delivered
    await producer.flush()
    await producer.close()

    consumer = AIOConsumer(
        {
            "bootstrap.servers": kafka_bootstrap,
            "group.id": f"spike-{uuid.uuid4().hex[:8]}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    await consumer.subscribe([topic])
    msg = None
    try:
        async with asyncio.timeout(30):
            while msg is None:
                candidate = await consumer.poll(1.0)
                if candidate is not None and candidate.error() is None:
                    msg = candidate
        await consumer.commit(message=msg, asynchronous=False)
    finally:
        await consumer.close()

    assert msg.key() == b"thread-1"
    assert msg.value() == b'{"x":1}'


async def test_aio_producer_rejects_per_message_headers(kafka_bootstrap: str, topic: str) -> None:
    """Pinned 2.16.0 behaviour: headers are not supported on the async producer."""
    producer = AIOProducer({"bootstrap.servers": kafka_bootstrap})
    try:
        with pytest.raises(NotImplementedError, match="Headers are not supported"):
            await producer.produce(topic, value=b"v", key=b"k", headers=[("traceparent", b"x")])
    finally:
        await producer.close()
