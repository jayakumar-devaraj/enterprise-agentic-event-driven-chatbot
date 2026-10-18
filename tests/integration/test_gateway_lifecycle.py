"""Gateway state lifecycle against real Redis and Kafka: thread ownership, acceptance recovery."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import redis.asyncio as aioredis
from chat_common.schemas import Envelope
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from confluent_kafka.aio import AIOConsumer
from conftest import KAFKA, REDIS_URL
from gateway import auth
from gateway.app import Deps, create_app, request_binding
from gateway.producer import KafkaEnvelopeProducer

pytestmark = pytest.mark.integration

ALICE = ("alice", "alice-pw")
BOB = ("bob", "bob-pw")


async def _deps() -> Deps:
    """Real Redis and real Kafka producer; users alice and bob."""
    settings = Settings()
    return Deps(
        settings=settings,
        redis=aioredis.from_url(REDIS_URL, decode_responses=True),
        producer=KafkaEnvelopeProducer(KAFKA, settings.kafka_topic),
        scrub=get_scrubber().ascrub,
        users={"alice": auth.hash_password("alice-pw"), "bob": auth.hash_password("bob-pw")},
    )


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Gateway app with its lifespan running."""
    app = create_app(_deps)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            yield c


def _body(thread: str) -> dict[str, str]:
    """A valid ChatRequest body with a fresh message_id."""
    return {"message": "hello", "thread_id": thread, "message_id": str(uuid.uuid4())}


async def test_owner_binding_has_no_expiry(client) -> None:
    """The real key carries no TTL, so it cannot lapse before the thread's checkpoints."""
    thread = f"own-{uuid.uuid4().hex[:8]}"
    assert (await client.post("/chat", json=_body(thread), auth=ALICE)).status_code == 202
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        assert await redis.ttl(auth.owner_key(thread)) == -1
    finally:
        await redis.aclose()
    assert (await client.post("/chat", json=_body(thread), auth=BOB)).status_code == 403


async def _count_records(message_id: str, settle_s: float = 5.0) -> int:
    """Records on chat-requests carrying `message_id`, read from the start until quiet."""
    consumer = AIOConsumer(
        {
            "bootstrap.servers": KAFKA,
            "group.id": f"acc-{uuid.uuid4().hex[:8]}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    await consumer.subscribe([Settings().kafka_topic])
    found, idle = 0, 0.0
    try:
        async with asyncio.timeout(90):
            while idle < settle_s:
                msg = await consumer.poll(1.0)
                if msg is None:
                    idle += 1.0
                    continue
                idle = 0.0
                if not msg.error() and Envelope.model_validate_json(msg.value()).message_id == (
                    uuid.UUID(message_id)
                ):
                    found += 1
    finally:
        await consumer.close()
    return found


async def test_claim_left_by_a_crash_is_recovered_by_the_retry(client) -> None:
    """A process that died after claiming the id but before the ack left `accepted:` behind.

    The client's retry must reach Kafka, then a further repeat returns 202 without producing.
    """
    thread = f"acc-{uuid.uuid4().hex[:8]}"
    body = _body(thread)
    assert (await client.post("/chat", json=_body(thread), auth=ALICE)).status_code == 202
    scrubbed = await get_scrubber().ascrub(body["message"])
    crashed = Envelope(
        message_id=uuid.UUID(body["message_id"]),
        thread_id=thread,
        user_id="alice",
        text=scrubbed,
        trace_id="x",
    )
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        await redis.set(f"accepted:{body['message_id']}", request_binding(crashed), ex=600)
    finally:
        await redis.aclose()

    assert (await client.post("/chat", json=body, auth=ALICE)).status_code == 202
    assert (await client.post("/chat", json=body, auth=ALICE)).status_code == 202
    assert await _count_records(body["message_id"]) == 1
