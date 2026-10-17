"""A1: PII posted to the gateway never reaches the Kafka record; typed placeholders do."""

import asyncio
import uuid

import httpx
import pytest
import redis.asyncio as aioredis
from chat_common.schemas import Envelope
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from confluent_kafka.aio import AIOConsumer
from conftest import KAFKA, REDIS_URL
from gateway import auth
from gateway.app import Deps, create_app
from gateway.producer import KafkaEnvelopeProducer

pytestmark = pytest.mark.integration

RAW = {
    "EMAIL_ADDRESS": "jane.doe@example.com",
    "PHONE_NUMBER": "212-555-0187",
    "CREDIT_CARD": "4111 1111 1111 1111",
    "US_SSN": "536-22-8726",
    "IP_ADDRESS": "192.168.10.24",
}


async def _deps() -> Deps:
    """Real Redis and real Kafka producer; test user alice."""
    settings = Settings()
    return Deps(
        settings=settings,
        redis=aioredis.from_url(REDIS_URL, decode_responses=True),
        producer=KafkaEnvelopeProducer(KAFKA, settings.kafka_topic),
        scrub=get_scrubber().ascrub,
        users={"alice": auth.hash_password("alice-pw")},
    )


async def _consume_envelope(message_id: str) -> Envelope:
    """Read chat-requests from the beginning until the record with `message_id` appears."""
    consumer = AIOConsumer(
        {
            "bootstrap.servers": KAFKA,
            "group.id": f"a1-{uuid.uuid4().hex[:8]}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    await consumer.subscribe([Settings().kafka_topic])
    try:
        async with asyncio.timeout(60):
            while True:
                msg = await consumer.poll(1.0)
                if msg is None or msg.error():
                    continue
                env = Envelope.model_validate_json(msg.value())
                if str(env.message_id) == message_id:
                    assert msg.key() == env.thread_id.encode()
                    return env
    finally:
        await consumer.close()


async def test_consumed_record_has_placeholders_not_raw_values() -> None:
    """POST a message with email, phone, card, SSN and IP; inspect the consumed record."""
    message = (
        f"Email {RAW['EMAIL_ADDRESS']}, phone {RAW['PHONE_NUMBER']}, card {RAW['CREDIT_CARD']}, "
        f"SSN {RAW['US_SSN']}, IP {RAW['IP_ADDRESS']}."
    )
    mid = str(uuid.uuid4())
    thread = f"a1-{uuid.uuid4().hex[:8]}"
    app = create_app(_deps)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                "/chat",
                json={"message": message, "thread_id": thread, "message_id": mid},
                auth=("alice", "alice-pw"),
            )
    assert r.status_code == 202, r.text

    env = await _consume_envelope(mid)
    for entity, raw in RAW.items():
        assert raw not in env.text, entity
        assert f"<{entity}>" in env.text, entity
