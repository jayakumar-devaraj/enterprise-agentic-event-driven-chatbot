"""The gateway's Kafka producer under sustained concurrent sends (real broker).

Each `POST /chat` waits for the broker ack before its 202 (SPEC §2), so produce latency is POST
latency. The 2026-10-24 rerun showed sends timing out only while requests kept arriving
(`docs/reports/latency-2026-10-24.md`).
"""

import asyncio
import statistics
import time
import uuid

import pytest
from chat_common.schemas import Envelope
from confluent_kafka.admin import AdminClient, NewTopic
from conftest import KAFKA
from gateway.producer import KafkaEnvelopeProducer

pytestmark = pytest.mark.integration
SENDERS, SENDS_EACH = 20, 10


async def test_sustained_concurrent_sends_are_all_acked_quickly() -> None:
    """20 senders, each sending back to back: every send is acked, p95 under 1 s."""
    topic = f"it-prod-{uuid.uuid4().hex[:8]}"
    admin = AdminClient({"bootstrap.servers": KAFKA})
    for fut in admin.create_topics([NewTopic(topic, 4, 1)]).values():
        fut.result(60)
    producer = KafkaEnvelopeProducer(KAFKA, topic)
    latencies: list[float] = []

    async def sender(n: int) -> None:
        for i in range(SENDS_EACH):
            env = Envelope(
                message_id=uuid.uuid4(), thread_id=f"t{n}-{i}", user_id="u", text="x", trace_id="t"
            )
            started = time.perf_counter()
            await producer.send(env)  # DependencyUnavailable on a timeout fails the test
            latencies.append(time.perf_counter() - started)

    try:
        await asyncio.gather(*(sender(n) for n in range(SENDERS)))
    finally:
        await producer.close()
        admin.delete_topics([topic])
    assert len(latencies) == SENDERS * SENDS_EACH
    assert statistics.quantiles(latencies, n=100)[94] < 1.0
