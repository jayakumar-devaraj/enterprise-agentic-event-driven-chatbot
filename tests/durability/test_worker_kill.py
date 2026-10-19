"""A5 live: SIGKILL the worker container repeatedly while it processes; nothing lost or doubled.

Builds and runs the real worker image (mock engine) in an isolated compose project (`chatbot-dur`,
other host ports), produces messages straight to Kafka on several threads, kills and restarts the
worker while they are in flight, then checks every message and every thread's history:
    uv run pytest -m durability
"""

import asyncio
import json
import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import redis.asyncio as aioredis
from chat_common.schemas import Envelope
from confluent_kafka import Producer
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from worker.graph import build_graph
from worker.llm import MockEngine

pytestmark = pytest.mark.durability

ROOT = Path(__file__).resolve().parents[2]
FILES = [
    "-f",
    str(ROOT / "deploy" / "compose.yaml"),
    "-f",
    str(Path(__file__).with_name("a5.override.yaml")),
]
PROJECT = "chatbot-dur"
ENV = {
    **os.environ,
    "REDIS_HOST_PORT": "26379",
    "KAFKA_HOST_PORT": "29092",
    "QDRANT_HOST_PORT": "26333",
    "QDRANT_GRPC_HOST_PORT": "26334",
    "MOCK_LLM": "true",
}
KAFKA, REDIS_URL = "127.0.0.1:29092", "redis://127.0.0.1:26379/0"
THREADS, PER_THREAD, KILLS = 6, 50, 3


def _compose(*args: str, timeout: int = 900) -> None:
    """docker compose against the isolated project with the A5 overlay; fail loudly."""
    cmd = ["docker", "compose", "-p", PROJECT, *FILES, *args]
    subprocess.run(cmd, env=ENV, check=True, capture_output=True, timeout=timeout)


def _kill(replica: str) -> None:
    """SIGKILL one replica of the isolated project (`compose kill` would hit every replica)."""
    subprocess.run(["docker", "kill", f"{PROJECT}-{replica}"], check=True, capture_output=True)


@pytest.fixture(scope="module")
def stack() -> Iterator[None]:
    """Fresh infra + seed + one worker built from this checkout; removed afterwards."""
    _compose("down", "-v")
    _compose("up", "-d", "--build", "--wait", "worker")
    yield
    _compose("down", "-v")


def _produce(envelopes: list[Envelope]) -> None:
    """Keyed by thread, so each thread keeps its order on one partition."""
    producer = Producer({"bootstrap.servers": KAFKA, "enable.idempotence": True})
    for env in envelopes:
        producer.produce("chat-requests", value=env.model_dump_json().encode(), key=env.thread_id)
    assert producer.flush(60) == 0


async def _terminal_count(redis: aioredis.Redis, envelopes: list[Envelope]) -> int:
    """Messages whose stream already ends in a terminal event."""
    done = 0
    for env in envelopes:
        rows = await redis.xrevrange(f"tokens:{env.thread_id}:{env.message_id}", count=1)
        if rows and json.loads(rows[0][1]["data"])["type"] != "token":
            done += 1
    return done


class _Unused:
    """Router test double: only the compiled graph's state reader is used here."""

    async def route(self, text: str) -> None:
        """Never called."""
        raise AssertionError


def _envelopes(prefix: str, threads: int, per_thread: int) -> tuple[list[str], list[Envelope]]:
    """`per_thread` messages on each of `threads` new threads, interleaved across threads."""
    run = uuid.uuid4().hex[:6]
    names = [f"{prefix}-{run}-{i}" for i in range(threads)]
    envelopes = [
        Envelope(
            message_id=uuid.uuid4(),
            thread_id=t,
            user_id="u",
            text=f"message {n} on {t}",
            ts=datetime.now(UTC),
            trace_id="t",
        )
        for n in range(per_thread)
        for t in names
    ]
    return names, envelopes


async def _assert_each_processed_once(
    redis: aioredis.Redis, threads: list[str], envelopes: list[Envelope], per_thread: int
) -> None:
    """Wait for all; then every message: one `done`, contiguous seq; every thread: its turns in
    order, one user+assistant pair per message."""
    deadline = time.monotonic() + 300
    while await _terminal_count(redis, envelopes) < len(envelopes):
        assert time.monotonic() < deadline, "messages still unfinished after 300 s"
        await asyncio.sleep(1)

    for env in envelopes:
        rows = await redis.xrange(f"tokens:{env.thread_id}:{env.message_id}")
        events = [json.loads(f["data"]) for _, f in rows]
        assert [e["seq"] for e in events] == list(range(len(events))), env.message_id
        assert [e["type"] for e in events].count("done") == 1, events[-3:]
        assert events[-1]["type"] == "done", events[-1]
        assert await redis.get(f"dedupe:{env.message_id}") == "done"

    async with AsyncRedisSaver.from_conn_string(REDIS_URL) as saver:
        graph = build_graph(_Unused(), MockEngine(), _Unused.route, 256).compile(  # type: ignore[arg-type]
            checkpointer=saver
        )
        for t in threads:
            snapshot = await graph.aget_state({"configurable": {"thread_id": t}})
            users = [x["text"] for x in snapshot.values["turns"] if x["role"] == "user"]
            assert users == [f"message {n} on {t}" for n in range(per_thread)], t
            assert len(snapshot.values["turns"]) == 2 * per_thread, t


async def test_a5_sigkill_during_processing_loses_and_duplicates_nothing(stack) -> None:
    """Every message: one `done`, contiguous seq; every thread: one user+assistant pair each."""
    threads, envelopes = _envelopes("a5", THREADS, PER_THREAD)
    _produce(envelopes)
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    kills_with_work_pending = 0
    try:
        for _ in range(KILLS):
            await asyncio.sleep(1.5)
            if await _terminal_count(redis, envelopes) < len(envelopes):
                kills_with_work_pending += 1
            _compose("kill", "worker")
            _compose("up", "-d", "--wait", "--no-build", "worker")
        await _assert_each_processed_once(redis, threads, envelopes, PER_THREAD)
    finally:
        await redis.aclose()
    assert kills_with_work_pending >= 1, "no kill landed while messages were in flight"


async def test_rebalance_while_processing_loses_and_duplicates_nothing(stack) -> None:
    """Partitions move between consumers mid-flight: scale out, SIGKILL one replica, scale in.

    Each step rebalances the consumer group, so a partition's uncommitted records are redelivered
    to a new owner while the old one may still hold their dedupe lease (ADR 0009).
    """
    threads, envelopes = _envelopes("rb", 2 * THREADS, PER_THREAD // 2)
    _produce(envelopes)
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    steps_with_work_pending = 0
    try:
        for step in (
            ("up", "-d", "--wait", "--no-build", "--scale", "worker=3", "worker"),
            ("kill", "worker-2"),
            ("up", "-d", "--wait", "--no-build", "--scale", "worker=2", "worker"),
            ("up", "-d", "--wait", "--no-build", "--scale", "worker=1", "worker"),
        ):
            await asyncio.sleep(1.5)
            if await _terminal_count(redis, envelopes) < len(envelopes):
                steps_with_work_pending += 1
            if step[0] == "kill":
                _kill(step[1])
            else:
                _compose(*step)
        await _assert_each_processed_once(redis, threads, envelopes, PER_THREAD // 2)
    finally:
        await redis.aclose()
    assert steps_with_work_pending >= 2, "rebalances did not land while messages were in flight"
