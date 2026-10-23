"""Restore an older Redis backup, rewind the worker group, recover every outcome once (SPEC §12).

Real worker image (mock engine), Redis, Kafka, Qdrant and seed in the isolated `chatbot-dur`
project. Batch A is processed, Redis is backed up, batch B (with a refund request and its approval)
is processed, then the backup is restored, which loses B's outcomes, its refund receipt and its
thread bindings. Rewinding the consumer group to the earliest retained record replays everything.
A, already done in the backup, is skipped, and B is rebuilt once:
    uv run pytest -m durability
"""

import asyncio
import json
import subprocess
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
import redis.asyncio as aioredis
from chat_common.schemas import Envelope
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from test_worker_kill import PROJECT, REDIS_URL, _compose, _produce, _terminal_count, _Unused
from worker import approval
from worker.graph import build_graph
from worker.llm import MockEngine

pytestmark = pytest.mark.durability

REDIS_IMAGE = "redis:8.10.2"
DATA, BACKUP = f"{PROJECT}_redis-data", f"{PROJECT}_backup"
REFUND_TEXT = "I want a refund for my last payment."  # a seed exemplar: routes to billing


def _docker(*args: str) -> None:
    """Plain docker command; fail loudly."""
    subprocess.run(["docker", *args], check=True, capture_output=True, timeout=300)


@pytest.fixture(scope="module")
def stack() -> Iterator[None]:
    """Fresh infra + seed + one worker built from this checkout; removed afterwards."""
    _compose("down", "-v")
    _compose("up", "-d", "--build", "--wait", "worker")
    yield
    _compose("down", "-v")
    subprocess.run(["docker", "volume", "rm", "-f", BACKUP], capture_output=True)


def _backup() -> None:
    """The README procedure: stop Redis, tar its volume, start it again."""
    _compose("stop", "redis")
    _docker("run", "--rm", "-v", f"{DATA}:/data:ro", "-v", f"{BACKUP}:/backup", REDIS_IMAGE,
            "tar", "-C", "/data", "-cf", "/backup/redis.tar", ".")  # fmt: skip
    _compose("up", "-d", "--wait", "redis")


def _restore() -> None:
    """The README procedure: replace the Redis volume with the backup."""
    _compose("stop", "redis")
    _compose("rm", "-f", "redis")
    _docker("volume", "rm", DATA)
    _docker("volume", "create", DATA)
    _docker("run", "--rm", "-v", f"{DATA}:/data", "-v", f"{BACKUP}:/backup:ro", REDIS_IMAGE,
            "tar", "-C", "/data", "-xf", "/backup/redis.tar")  # fmt: skip
    _compose("up", "-d", "--wait", "redis")


def _rewind() -> None:
    """The README procedure: with no worker running, replay the topic from its earliest record."""
    _compose("exec", "-T", "kafka", "/opt/kafka/bin/kafka-consumer-groups.sh",
             "--bootstrap-server", "kafka:9092", "--group", "chat-workers",
             "--topic", "chat-requests", "--reset-offsets", "--to-earliest",
             "--execute")  # fmt: skip


def _chat(thread: str, text: str) -> Envelope:
    """A chat envelope from user `u`."""
    return Envelope(
        message_id=uuid.uuid4(), thread_id=thread, user_id="u", text=text,
        ts=datetime.now(UTC), trace_id="t",
    )  # fmt: skip


async def _wait_terminal(envelopes: list[Envelope]) -> None:
    """Until every message's stream ends in a terminal event (a fresh client per call)."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        deadline = time.monotonic() + 300
        while await _terminal_count(redis, envelopes) < len(envelopes):
            assert time.monotonic() < deadline, "messages still unfinished after 300 s"
            await asyncio.sleep(1)
    finally:
        await redis.aclose()


async def _refunds(redis: aioredis.Redis, ref: uuid.UUID) -> int:
    """Refund records for one originating message."""
    rows = await redis.xrange(approval.REFUNDS_STREAM)
    return sum(1 for _, f in rows if f["ref_message_id"] == str(ref))


async def _events(redis: aioredis.Redis, env: Envelope) -> list[tuple[str, dict]]:
    """(entry id, event) for one message's stream."""
    rows = await redis.xrange(f"tokens:{env.thread_id}:{env.message_id}")
    return [(i, json.loads(f["data"])) for i, f in rows]


async def test_restore_then_rewind_recovers_each_outcome_once(stack) -> None:
    """A untouched; B, the refund and the thread bindings rebuilt exactly once, in order."""
    run = uuid.uuid4().hex[:6]
    threads = [f"rs-{run}-{i}" for i in range(4)]
    a = [_chat(t, f"message {n} on {t}") for n in range(5) for t in threads]
    _produce(a)
    await _wait_terminal(a)
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    a_streams = {env.message_id: await _events(redis, env) for env in a}
    await redis.aclose()

    _compose("stop", "worker")
    _backup()
    _compose("up", "-d", "--wait", "--no-build", "worker")

    b = [_chat(t, f"message {n} on {t}") for n in range(5, 10) for t in threads]
    refund_thread = f"rs-{run}-refund"
    ask = _chat(refund_thread, REFUND_TEXT)
    _produce([*b, ask])
    await _wait_terminal([*b, ask])
    decision = Envelope(
        message_id=uuid.uuid4(), thread_id=refund_thread, user_id="u", text="",
        ts=datetime.now(UTC), trace_id="t", kind="approval", ref_message_id=ask.message_id,
        approved=True,
    )  # fmt: skip
    _produce([decision])
    await _wait_terminal([decision])
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    assert await _refunds(redis, ask.message_id) == 1
    await redis.aclose()

    _compose("stop", "worker")
    _restore()
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    # The restore lost everything after the backup: B's streams, the refund, the new binding.
    assert await _refunds(redis, ask.message_id) == 0
    assert await redis.get(f"thread_owner:{refund_thread}") is None
    assert not await redis.exists(f"tokens:{refund_thread}:{decision.message_id}")
    await redis.aclose()

    _rewind()
    _compose("up", "-d", "--wait", "--no-build", "worker")
    await _wait_terminal([*a, *b, ask, decision])

    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        for env in a:  # done in the backup: skipped, not reprocessed
            assert await _events(redis, env) == a_streams[env.message_id], env.message_id
        for env in [*b, decision]:
            events = [e for _, e in await _events(redis, env)]
            assert [e["seq"] for e in events] == list(range(len(events))), env.message_id
            assert [e["type"] for e in events].count("done") == 1, events[-3:]
        assert await _refunds(redis, ask.message_id) == 1
        assert await redis.get(f"refund_applied:{ask.message_id}") is not None
        for t in [*threads, refund_thread]:
            assert await redis.get(f"thread_owner:{t}") == "u", t

        async with AsyncRedisSaver.from_conn_string(REDIS_URL) as saver:
            graph = build_graph(_Unused(), MockEngine(), _Unused.route, 256).compile(  # type: ignore[arg-type]
                checkpointer=saver
            )
            for t in threads:
                state = await graph.aget_state({"configurable": {"thread_id": t}})
                users = [x["text"] for x in state.values["turns"] if x["role"] == "user"]
                assert users == [f"message {n} on {t}" for n in range(10)], t
                assert len(state.values["turns"]) == 20, t
            state = await graph.aget_state({"configurable": {"thread_id": refund_thread}})
            texts = [x["text"] for x in state.values["turns"]]
            assert texts == [REFUND_TEXT, approval.APPROVED_TEXT]
    finally:
        await redis.aclose()
