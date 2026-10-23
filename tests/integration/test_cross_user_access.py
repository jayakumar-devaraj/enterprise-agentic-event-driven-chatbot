"""Another user against live data: retained checkpoint, token stream and pending refund (SPEC §3).

Real gateway app and real Redis; the worker graph runs in-process on the envelope the gateway
produced. Every request bob makes on alice's thread is 403, produces nothing, and leaves alice's
data unchanged; alice can still act on it afterwards.
"""

import uuid
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from chat_common.schemas import Envelope
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from conftest import REDIS_URL
from gateway import auth
from gateway.app import Deps, create_app
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from worker import approval
from worker.graph import build_graph, make_processor
from worker.lease import Lease
from worker.llm import MockEngine
from worker.router import Route
from worker.streams import StreamWriter

pytestmark = pytest.mark.integration
ALICE, BOB = ("alice", "alice-pw"), ("bob", "bob-pw")
USERS = {name: auth.hash_password(pw) for name, pw in (ALICE, BOB)}


class Producer:
    """Test double for Kafka: records what the gateway produced."""

    def __init__(self) -> None:
        """Nothing produced yet."""
        self.sent: list[Envelope] = []

    async def send(self, envelope: Envelope) -> None:
        """Record."""
        self.sent.append(envelope)

    async def ready(self) -> None:
        """Always ready."""

    async def close(self) -> None:
        """No-op."""


class Billing:
    """Test double router: billing, no injection (Qdrant routing is covered by A2)."""

    async def route(self, text: str) -> Route:
        """Fixed verdict."""
        return Route(intent="billing", score=0.9, injection_score=0.0, injection=False)


async def _snapshot(redis: Any, graph: Any, thread: str, mid: str) -> tuple[Any, ...]:
    """Everything bob's requests must leave untouched."""
    state = await graph.aget_state({"configurable": {"thread_id": thread}})
    return (
        await redis.xrange(f"tokens:{thread}:{mid}"),
        state.values,
        state.next,
        await redis.get(approval.pending_key(thread, mid)),
        await redis.get(auth.owner_key(thread)),
    )


async def test_other_user_cannot_reach_live_thread_data() -> None:
    """Chat, stream and approve by bob are 403 against alice's live checkpoint and stream."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    producer = Producer()
    thread = f"xu-{uuid.uuid4().hex[:8]}"

    async def deps() -> Deps:
        settings = Settings(stream_block_ms=200, stream_ttl_s=2)
        return Deps(settings, redis, producer, get_scrubber().ascrub, USERS)

    app = create_app(deps)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
        AsyncRedisSaver.from_conn_string(REDIS_URL, ttl={"default_ttl": 5}) as saver,
    ):
        await saver.asetup()
        graph = build_graph(Billing(), MockEngine(), get_scrubber().ascrub, 256, redis=redis)
        compiled = graph.compile(checkpointer=saver)
        ask = {"message": "I want a refund", "thread_id": thread, "message_id": str(uuid.uuid4())}
        assert (await client.post("/chat", json=ask, auth=ALICE)).status_code == 202
        (env,) = producer.sent
        lease = Lease(f"dedupe:{env.message_id}")
        await redis.set(lease.key, lease.token, ex=60)
        writer = StreamWriter(redis, thread, env.message_id, lease=lease, maxlen=100, ttl_s=60)
        await make_processor(compiled, redis=redis)(env, writer)
        mid = str(env.message_id)
        before = await _snapshot(redis, compiled, thread, mid)
        assert before[2] == ("await_approval",) and before[3] == "alice"

        decision = {"message_id": str(uuid.uuid4()), "approved": True}
        codes = [
            (await client.post("/chat", json={**ask, "message_id": str(uuid.uuid4())}, auth=BOB)),
            (await client.get(f"/stream/{thread}/{mid}", auth=BOB)),
            (await client.post(f"/approve/{thread}/{mid}", json=decision, auth=BOB)),
        ]
        assert [r.status_code for r in codes] == [403, 403, 403]
        assert len(producer.sent) == 1
        assert await _snapshot(redis, compiled, thread, mid) == before

        mine = await client.post(f"/approve/{thread}/{mid}", json=decision, auth=ALICE)
        assert mine.status_code == 202 and len(producer.sent) == 2
    await redis.aclose()
