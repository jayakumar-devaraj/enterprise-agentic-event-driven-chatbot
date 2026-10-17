"""A3: SSE ordering, late join from 0, Last-Event-ID resume, per-turn isolation (real Redis)."""

import json
import uuid

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
from worker.graph import build_graph, make_processor
from worker.llm import MockEngine
from worker.router import Route
from worker.streams import StreamWriter

pytestmark = pytest.mark.integration
CREDS = ("alice", "alice-pw")


class _NoProducer:
    """Test double: the stream endpoint never produces."""

    async def send(self, envelope: Envelope) -> None:
        """Unused."""
        raise AssertionError("not expected")

    async def close(self) -> None:
        """No-op."""


class _Router:
    """Test double: always billing."""

    async def route(self, text: str) -> Route:
        """Fixed verdict."""
        return Route(intent="billing", score=0.9, injection_score=0.0, injection=False)


def parse_sse(body: str) -> list[dict]:
    """Parse `id:` / `event:` / `data:` frames; keep-alive comments are skipped."""
    frames = []
    for block in body.strip().split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if line and not line.startswith(":")
        )
        if fields:
            frames.append(
                {"id": fields["id"], "event": fields["event"], **json.loads(fields["data"])}
            )
    return frames


async def _run_turn(thread: str, text: str) -> uuid.UUID:
    """Process one turn through the real graph (mock engine) and Redis checkpointer."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    await redis.set(auth.owner_key(thread), "alice", ex=600)
    env = Envelope(
        message_id=uuid.uuid4(), thread_id=thread, user_id="alice", text=text, trace_id="t"
    )
    writer = StreamWriter(redis, thread, env.message_id, maxlen=100, ttl_s=120)
    async with AsyncRedisSaver.from_conn_string(REDIS_URL, ttl={"default_ttl": 5}) as saver:
        await saver.asetup()
        graph = build_graph(_Router(), MockEngine(), get_scrubber().ascrub, 256)
        await make_processor(graph.compile(checkpointer=saver))(env, writer)
    await redis.aclose()
    return env.message_id


async def _get(thread: str, mid: uuid.UUID, last_id: str | None = None) -> httpx.Response:
    """GET the SSE stream through the real gateway app (real Redis)."""

    async def deps() -> Deps:
        return Deps(
            settings=Settings(stream_block_ms=500, stream_ttl_s=10),
            redis=aioredis.from_url(REDIS_URL, decode_responses=True),
            producer=_NoProducer(),
            scrub=get_scrubber().ascrub,
            users={"alice": auth.hash_password("alice-pw"), "bob": auth.hash_password("bob-pw")},
        )

    app = create_app(deps)
    headers = {"Last-Event-ID": last_id} if last_id else {}
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.get(f"/stream/{thread}/{mid}", headers=headers, auth=CREDS)


async def test_a3_ordering_late_join_resume_and_turn_isolation() -> None:
    """All four A3 properties on one two-turn thread."""
    thread = f"a3-{uuid.uuid4().hex[:8]}"
    first_mid = await _run_turn(thread, "Why was I charged twice?")
    second_mid = await _run_turn(thread, "Please fix it")

    # Late connection (the turn already finished) still receives everything from seq 0.
    r = await _get(thread, first_mid)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    first = parse_sse(r.text)
    assert [f["seq"] for f in first] == list(range(len(first)))
    assert first[-1]["event"] == "done" and [f["event"] for f in first].count("done") == 1

    # Resume after the second event: exactly the remainder, no gap, no duplicate.
    resumed = parse_sse((await _get(thread, first_mid, last_id=first[1]["id"])).text)
    assert resumed == first[2:]

    # The second turn's stream holds only its own events.
    second = parse_sse((await _get(thread, second_mid)).text)
    assert [f["seq"] for f in second] == list(range(len(second)))
    assert {f["id"] for f in second}.isdisjoint({f["id"] for f in first})
    tokens = " ".join(f["token"] for f in second if f["event"] == "token")
    assert tokens == "[mock] intent=billing turns=2 last=Why was I charged tw"


async def test_stream_requires_owner() -> None:
    """Another user's thread is 403 before any event is sent."""
    thread = f"a3o-{uuid.uuid4().hex[:8]}"
    mid = await _run_turn(thread, "hello")

    async def deps() -> Deps:
        return Deps(
            settings=Settings(),
            redis=aioredis.from_url(REDIS_URL, decode_responses=True),
            producer=_NoProducer(),
            scrub=get_scrubber().ascrub,
            users={"bob": auth.hash_password("bob-pw")},
        )

    app = create_app(deps)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get(f"/stream/{thread}/{mid}", auth=("bob", "bob-pw"))
            bad = await client.get(f"/stream/bad id/{mid}", auth=("bob", "bob-pw"))
    assert r.status_code == 403
    assert bad.status_code == 422
