"""Redelivery after a crash at each durability boundary: one logical turn, resumable output.

Real Redis checkpointer, streams, dedupe and audit; the production graph with the mock engine. A
`Crash` (BaseException, so no handler catches it) stands in for the process dying; the redelivery
is the same record handled again once the dedupe lease has expired.
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import redis.asyncio as aioredis
from chat_common.schemas import Envelope
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from conftest import REDIS_URL
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from worker import approval
from worker.consumer import Worker
from worker.graph import build_graph, make_processor
from worker.llm import MockEngine
from worker.router import Route
from worker.streams import StreamWriter

pytestmark = pytest.mark.integration
# 0.5 * (1 + 1) + 0.2 * 1 = 1.2 < 2: satisfies the lease formula with a short lease.
SETTINGS = Settings(
    inference_timeout_s=0.5, max_retries=1, retry_backoff_max_s=0.2, dedupe_lease_s=2
)


class Crash(BaseException):
    """The worker process dying at this point."""


class Billing:
    """Test double router: billing, no injection (Qdrant routing is covered by A2)."""

    async def route(self, text: str) -> Route:
        """Fixed verdict."""
        return Route(intent="billing", score=0.9, injection_score=0.0, injection=False)


class Msg:
    """Test double for a Kafka record carrying one envelope."""

    def __init__(self, env: Envelope) -> None:
        """Serialize the envelope."""
        self._v = env.model_dump_json().encode()

    def value(self) -> bytes:
        """Payload."""
        return self._v

    def key(self) -> bytes:
        """Key."""
        return b"k"

    def error(self) -> None:
        """No error."""


class Consumer:
    """Test double: counts commits."""

    def __init__(self) -> None:
        """No commits."""
        self.commits = 0

    async def commit(self, message: Any, asynchronous: bool) -> None:
        """Record a commit."""
        self.commits += 1


class NoDLQ:
    """Test double: nothing in these scenarios may reach the DLQ."""

    async def produce(self, *args: Any, **kwargs: Any) -> Any:
        """Fail the test."""
        raise AssertionError("unexpected DLQ write")


@pytest.fixture
async def stack() -> AsyncIterator[tuple[Any, Worker, Consumer]]:
    """Real Redis + checkpointer + compiled graph behind a Worker."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    async with AsyncRedisSaver.from_conn_string(REDIS_URL, ttl={"default_ttl": 5}) as saver:
        await saver.asetup()
        graph = build_graph(Billing(), MockEngine(), get_scrubber().ascrub, 256, redis=redis)
        process = make_processor(graph.compile(checkpointer=saver), redis=redis)
        consumer = Consumer()
        worker = Worker(
            SETTINGS, consumer=consumer, dlq_producer=NoDLQ(), redis=redis, process=process
        )
        yield redis, worker, consumer, graph, saver
    await redis.aclose()


def _crash_on(monkeypatch, method: str, *, after_write: bool, nth: int = 1) -> None:
    """Make the nth call of StreamWriter.<method> crash, before or after it writes."""
    original = getattr(StreamWriter, method)
    calls = {"n": 0}

    async def patched(self: StreamWriter, *args: Any) -> str:
        calls["n"] += 1
        if calls["n"] != nth:
            return await original(self, *args)
        monkeypatch.setattr(StreamWriter, method, original)
        if after_write:
            await original(self, *args)
        raise Crash(method)

    monkeypatch.setattr(StreamWriter, method, patched)


async def _events(redis: Any, env: Envelope) -> list[tuple[str, dict]]:
    """(entry id, event) for the message's stream."""
    rows = await redis.xrange(f"tokens:{env.thread_id}:{env.message_id}")
    return [(i, json.loads(f["data"])) for i, f in rows]


async def _turns(saver: Any, graph: Any, thread: str) -> list[dict]:
    """Checkpointed conversation turns of a thread."""
    snapshot = await graph.compile(checkpointer=saver).aget_state(
        {"configurable": {"thread_id": thread}}
    )
    return snapshot.values.get("turns", [])


def _chat(thread: str, text: str) -> Envelope:
    """A chat envelope."""
    return Envelope(
        message_id=uuid.uuid4(), thread_id=thread, user_id="alice", text=text, trace_id="t"
    )


def _text(events: list[tuple[str, dict]]) -> str:
    """Joined token text."""
    return " ".join(e["token"] for _, e in events if e["type"] == "token")


async def test_crash_after_checkpoint_before_done(stack, monkeypatch) -> None:
    """The turn is checkpointed but `done` never written: redelivery finishes it, no second turn."""
    redis, worker, consumer, graph, saver = stack
    env = _chat(f"rp-{uuid.uuid4().hex[:8]}", "Why is my bill higher?")
    _crash_on(monkeypatch, "done", after_write=False)
    with pytest.raises(Crash):
        await worker.handle(Msg(env))
    partial = await _events(redis, env)

    await worker.handle(Msg(env))
    events = await _events(redis, env)
    assert [e["type"] for _, e in events][-1] == "done"
    assert [e["seq"] for _, e in events] == list(range(len(events)))
    assert _text(events) == _text(partial)
    assert len(await _turns(saver, graph, env.thread_id)) == 2
    assert consumer.commits == 1


async def test_crash_after_done_before_dedupe(stack, monkeypatch) -> None:
    """`done` is durable but the worker died before marking dedupe: the stream is left untouched."""
    redis, worker, consumer, graph, saver = stack
    env = _chat(f"rp-{uuid.uuid4().hex[:8]}", "Why is my bill higher?")
    _crash_on(monkeypatch, "done", after_write=True)
    with pytest.raises(Crash):
        await worker.handle(Msg(env))
    before = await _events(redis, env)

    await worker.handle(Msg(env))
    assert await _events(redis, env) == before
    assert len(await _turns(saver, graph, env.thread_id)) == 2
    assert await redis.get(f"dedupe:{env.message_id}") == "done"
    assert consumer.commits == 1


async def test_crash_mid_stream_restarts_at_seq_zero(stack, monkeypatch) -> None:
    """Partial tokens, no checkpointed reply: the retry replaces the stream from seq 0, once."""
    redis, worker, consumer, graph, saver = stack
    env = _chat(f"rp-{uuid.uuid4().hex[:8]}", "Why is my bill higher?")
    _crash_on(monkeypatch, "token", after_write=True, nth=2)
    with pytest.raises(Crash):
        await worker.handle(Msg(env))
    assert len(await _events(redis, env)) == 2

    await worker.handle(Msg(env))
    events = await _events(redis, env)
    assert [e["seq"] for _, e in events] == list(range(len(events)))
    assert events[-1][1]["type"] == "done"
    assert len(await _turns(saver, graph, env.thread_id)) == 2


async def test_approval_crash_after_settle_before_done(stack, monkeypatch) -> None:
    """Refund settled and checkpointed, then a crash: redelivery replies, never refunds again."""
    redis, worker, consumer, graph, saver = stack
    thread = f"rp-{uuid.uuid4().hex[:8]}"
    ask = _chat(thread, "I want a refund for last month")
    await worker.handle(Msg(ask))
    decision = Envelope(
        message_id=uuid.uuid4(),
        thread_id=thread,
        user_id="alice",
        text="",
        trace_id="t",
        kind="approval",
        ref_message_id=ask.message_id,
        approved=True,
    )
    _crash_on(monkeypatch, "done", after_write=False)
    with pytest.raises(Crash):
        await worker.handle(Msg(decision))

    await worker.handle(Msg(decision))
    events = await _events(redis, decision)
    assert _text(events) == approval.APPROVED_TEXT and events[-1][1]["type"] == "done"
    refunds = await redis.xrange(approval.REFUNDS_STREAM)
    assert sum(1 for _, f in refunds if f["ref_message_id"] == str(ask.message_id)) == 1
    assert len(await _turns(saver, graph, thread)) == 2


async def test_chat_while_approval_pending_abandons_it(stack) -> None:
    """A new chat supersedes the paused refund: pending cleared, audited, later approval refused."""
    redis, worker, consumer, graph, saver = stack
    thread = f"rp-{uuid.uuid4().hex[:8]}"
    ask = _chat(thread, "I want a refund for last month")
    await worker.handle(Msg(ask))
    assert await redis.get(approval.pending_key(thread, str(ask.message_id))) == "alice"

    follow = _chat(thread, "Why is my bill higher?")
    await worker.handle(Msg(follow))
    assert (await _events(redis, follow))[-1][1]["type"] == "done"
    assert await redis.get(approval.pending_key(thread, str(ask.message_id))) is None
    audit = [f for _, f in await redis.xrange("audit:events") if f["thread_id"] == thread]
    assert [f["event"] for f in audit] == ["refund_proposed", "refund_abandoned"]
