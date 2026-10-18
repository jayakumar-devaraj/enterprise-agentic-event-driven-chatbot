"""Gateway state lifecycle against real Redis and Kafka: thread ownership."""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import redis.asyncio as aioredis
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from conftest import KAFKA, REDIS_URL
from gateway import auth
from gateway.app import Deps, create_app
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
