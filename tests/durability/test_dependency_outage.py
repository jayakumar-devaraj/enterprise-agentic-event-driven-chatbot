"""A4 live: Qdrant stopped mid-run ⇒ ErrorEvent + DLQ + commit; restarted ⇒ the next message works.

Real Kafka consumer, Redis, checkpointer and Qdrant from an isolated compose project (`chatbot-dur`,
other host ports), running the production Worker, graph and Router with the mock engine:
    uv run pytest -m durability
"""

import asyncio
import json
import os
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import redis.asyncio as aioredis
from chat_common.schemas import Envelope
from chat_common.scrubber import get_scrubber
from chat_common.settings import Settings
from confluent_kafka import KafkaException, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka.aio import AIOConsumer, AIOProducer
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from qdrant_client import AsyncQdrantClient
from seed.main import seed
from worker.consumer import Worker
from worker.embedding import get_embedder
from worker.graph import build_graph, make_processor
from worker.llm import MockEngine
from worker.router import Router

pytestmark = pytest.mark.durability

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "compose.yaml"
PROJECT = "chatbot-dur"
ENV = {
    **os.environ,
    "REDIS_HOST_PORT": "26379",
    "KAFKA_HOST_PORT": "29092",
    "QDRANT_HOST_PORT": "26333",
    "QDRANT_GRPC_HOST_PORT": "26334",
}
KAFKA, REDIS_URL = "127.0.0.1:29092", "redis://127.0.0.1:26379/0"
QDRANT_URL, QDRANT_GRPC = "http://127.0.0.1:26333", 26334
# 3 * (1 + 1) + 0.2 * 1 = 6.2 < 10: satisfies the lease formula.
SETTINGS = Settings(
    qdrant_url=QDRANT_URL,
    inference_timeout_s=3,
    max_retries=1,
    retry_backoff_max_s=0.2,
    dedupe_lease_s=10,
)


def _compose(*args: str) -> None:
    """docker compose against the isolated project; fail loudly."""
    cmd = ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE), *args]
    subprocess.run(cmd, env=ENV, check=True, capture_output=True, timeout=300)


@pytest.fixture(scope="module")
def stack() -> Iterator[None]:
    """Fresh redis, kafka and qdrant; removed afterwards."""
    _compose("down", "-v")
    _compose("up", "-d", "--wait", "redis", "kafka", "qdrant")
    yield
    _compose("down", "-v")


@pytest.fixture
async def topics(stack) -> AsyncIterator[tuple[str, str]]:
    """Request and DLQ topics, and the Qdrant collections seeded by the real seed path."""
    await seed(SETTINGS)
    names = (f"a4-req-{uuid.uuid4().hex[:6]}", f"a4-dlq-{uuid.uuid4().hex[:6]}")
    admin = AdminClient({"bootstrap.servers": KAFKA})
    for fut in admin.create_topics([NewTopic(n, 1, 1) for n in names]).values():
        fut.result(60)
    yield names


async def _produce(topic: str, env: Envelope) -> None:
    """One envelope keyed by its thread."""
    producer = AIOProducer({"bootstrap.servers": KAFKA})
    await (await producer.produce(topic, value=env.model_dump_json().encode(), key=b"t"))
    await producer.flush()
    await producer.close()


async def _run_worker_until(topic: str, dlq: str, commits: int) -> None:
    """Production Worker + graph + Router until `commits` offsets are committed (≤ 120 s)."""
    group = f"a4-{uuid.uuid4().hex[:6]}"
    consumer = AIOConsumer(
        {
            "bootstrap.servers": KAFKA,
            "group.id": group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    dlq_producer = AIOProducer({"bootstrap.servers": KAFKA})
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    qdrant = AsyncQdrantClient(url=QDRANT_URL, grpc_port=QDRANT_GRPC, prefer_grpc=True)
    router = Router(
        get_embedder(SETTINGS.embed_model),
        qdrant,
        intents_collection=SETTINGS.qdrant_collection,
        injection_collection=SETTINGS.qdrant_injection_collection,
        benign_collection=SETTINGS.qdrant_benign_collection,
        route_threshold=SETTINGS.route_threshold,
        injection_margin=SETTINGS.injection_margin,
    )
    stop = asyncio.Event()
    async with AsyncRedisSaver.from_conn_string(REDIS_URL, ttl={"default_ttl": 5}) as saver:
        await saver.asetup()
        graph = build_graph(router, MockEngine(), get_scrubber().ascrub, 256, redis=redis)
        process = make_processor(graph.compile(checkpointer=saver), redis=redis)
        worker = Worker(
            SETTINGS,
            consumer=consumer,
            dlq_producer=dlq_producer,
            redis=redis,
            process=process,
            dlq_topic=dlq,
        )
        task = asyncio.create_task(worker.run(topic, stop))
        try:
            async with asyncio.timeout(120):
                while True:
                    try:
                        (tp,) = await consumer.committed([TopicPartition(topic, 0)], timeout=5)
                    except KafkaException as exc:
                        if not exc.args[0].retriable():
                            raise
                        tp = None
                    if tp is not None and tp.offset >= commits:
                        break
                    if task.done():
                        task.result()  # surface a crash instead of waiting for the timeout
                    await asyncio.sleep(0.5)
        finally:
            stop.set()
            await task
            await consumer.close()
            await dlq_producer.close()
            await qdrant.close()
            await redis.aclose()


async def _dlq_records(dlq: str, expected: int) -> list[dict]:
    """Read `expected` DLQ records."""
    consumer = AIOConsumer(
        {"bootstrap.servers": KAFKA, "group.id": uuid.uuid4().hex, "auto.offset.reset": "earliest"}
    )
    await consumer.subscribe([dlq])
    out: list[dict] = []
    try:
        async with asyncio.timeout(60):
            while len(out) < expected:
                msg = await consumer.poll(1.0)
                if msg is not None and not msg.error():
                    out.append(json.loads(msg.value()))
    finally:
        await consumer.close()
    return out


async def _events(env: Envelope) -> list[dict]:
    """The message's stream events."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        rows = await redis.xrange(f"tokens:{env.thread_id}:{env.message_id}")
        return [json.loads(f["data"]) for _, f in rows]
    finally:
        await redis.aclose()


def _env(thread: str, text: str) -> Envelope:
    """A chat envelope."""
    return Envelope(message_id=uuid.uuid4(), thread_id=thread, user_id="u", text=text, trace_id="t")


async def test_a4_qdrant_stopped_then_restarted(topics) -> None:
    """Outage ⇒ one ErrorEvent, one DLQ record, lease released, committed; recovery ⇒ done."""
    req, dlq = topics
    thread = f"a4-{uuid.uuid4().hex[:6]}"
    during, after = _env(thread, "Why was I charged twice?"), _env(thread, "My Wi-Fi is slow.")

    _compose("stop", "qdrant")
    try:
        await _produce(req, during)
        await _run_worker_until(req, dlq, commits=1)
    finally:
        _compose("up", "-d", "--wait", "qdrant")

    assert await _events(during) == [
        {
            "type": "error",
            "seq": 0,
            "code": "dependency_unavailable",
            "detail": "a dependency is unavailable; try again",
        }
    ]
    (record,) = await _dlq_records(dlq, 1)
    assert record["reason"] == "dependency_unavailable"
    assert record["envelope"]["message_id"] == str(during.message_id)
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    try:
        assert await redis.get(f"dedupe:{during.message_id}") is None
    finally:
        await redis.aclose()

    await _produce(req, after)
    await _run_worker_until(req, dlq, commits=2)
    events = await _events(after)
    assert events[-1]["type"] == "done" and events[0]["type"] == "token"
