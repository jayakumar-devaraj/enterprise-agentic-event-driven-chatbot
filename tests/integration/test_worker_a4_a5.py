"""A4 (malformed → DLQ) and A5 (duplicates, abandoned lease) against real Kafka and Redis."""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator

import pytest
import redis as redis_sync
import redis.asyncio as aioredis
from chat_common.schemas import Envelope
from chat_common.settings import Settings
from confluent_kafka import KafkaException, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic
from confluent_kafka.aio import AIOConsumer, AIOProducer
from conftest import KAFKA, REDIS_URL
from worker import dlq as dlq_tool
from worker.consumer import TransientDependencyError, Worker
from worker.streams import StreamWriter

pytestmark = pytest.mark.integration
# 0.5 * (1 + 1) + 0.2 * 1 = 1.2 < 3: satisfies the lease formula with short timings.
SETTINGS = Settings(
    inference_timeout_s=0.5, max_retries=1, retry_backoff_max_s=0.2, dedupe_lease_s=3
)


@pytest.fixture
async def topics() -> AsyncIterator[tuple[str, str]]:
    """Fresh request and DLQ topics per test."""
    suffix = uuid.uuid4().hex[:8]
    names = (f"it-req-{suffix}", f"it-dlq-{suffix}")
    admin = AdminClient({"bootstrap.servers": KAFKA})
    for fut in admin.create_topics([NewTopic(n, 1, 1) for n in names]).values():
        fut.result(60)
    yield names
    admin.delete_topics(list(names))


async def _produce(topic: str, values: list[bytes]) -> None:
    """Produce raw values keyed by a fixed thread."""
    producer = AIOProducer({"bootstrap.servers": KAFKA})
    for v in values:
        await (await producer.produce(topic, value=v, key=b"t-it"))
    await producer.flush()
    await producer.close()


async def _run_worker(
    topic: str, dlq: str, process, expected_commits: int, group: str | None = None
) -> None:
    """Run a real Worker until `group` (default: new) has committed `expected_commits` (≤ 60 s)."""
    group = group or f"it-{uuid.uuid4().hex[:8]}"
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
    stop = asyncio.Event()
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
        async with asyncio.timeout(60):
            while True:
                try:
                    (tp,) = await consumer.committed([TopicPartition(topic, 0)], timeout=5)
                except KafkaException as exc:
                    if not exc.args[0].retriable():  # e.g. NOT_COORDINATOR while groups load
                        raise
                    tp = None
                if tp is not None and tp.offset >= expected_commits:
                    break
                await asyncio.sleep(0.5)
    finally:
        stop.set()
        await task
        await consumer.close()
        await dlq_producer.close()
        await redis.aclose()


async def _read_topic(topic: str, expected: int) -> list[dict]:
    """Read `expected` JSON records from a topic."""
    consumer = AIOConsumer(
        {"bootstrap.servers": KAFKA, "group.id": uuid.uuid4().hex, "auto.offset.reset": "earliest"}
    )
    await consumer.subscribe([topic])
    out: list[dict] = []
    try:
        async with asyncio.timeout(30):
            while len(out) < expected:
                msg = await consumer.poll(1.0)
                if msg is not None and not msg.error():
                    out.append(json.loads(msg.value()))
    finally:
        await consumer.close()
    return out


async def _events(thread: str, mid: uuid.UUID) -> list[dict]:
    """All events in a message's stream."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    rows = await redis.xrange(f"tokens:{thread}:{mid}")
    await redis.aclose()
    return [json.loads(f["data"]) for _, f in rows]


def _env() -> Envelope:
    """A valid envelope on a unique thread."""
    return Envelope(
        message_id=uuid.uuid4(),
        thread_id=f"t-{uuid.uuid4().hex[:6]}",
        user_id="u",
        text="hi",
        trace_id="x",
    )


async def test_a4_malformed_envelope_lands_in_dlq(topics) -> None:
    """Garbage on the request topic ⇒ one DLQ record with reason=malformed."""
    req, dlq = topics
    await _produce(req, [b"{not an envelope"])

    async def never(env: Envelope, writer: StreamWriter) -> None:
        raise AssertionError("processor must not run for malformed input")

    await _run_worker(req, dlq, never, expected_commits=1)
    (record,) = await _read_topic(dlq, 1)
    assert record["reason"] == "malformed" and "original_b64" in record


async def test_a5_same_message_twice_one_done(topics) -> None:
    """The same envelope delivered twice ⇒ processed once, one stream with one done."""
    req, dlq = topics
    env = _env()
    calls: list[int] = []

    async def process(e: Envelope, writer: StreamWriter) -> None:
        calls.append(1)
        await writer.token("ok")
        await writer.done()

    raw = env.model_dump_json().encode()
    await _produce(req, [raw, raw])
    await _run_worker(req, dlq, process, expected_commits=2)
    events = await _events(env.thread_id, env.message_id)
    assert len(calls) == 1
    assert [e["type"] for e in events].count("done") == 1


async def test_a5_abandoned_lease_reprocessed_after_expiry(topics) -> None:
    """A crashed holder's `processing` lease blocks until it expires, then the message runs."""
    req, dlq = topics
    env = _env()
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    await redis.set(f"dedupe:{env.message_id}", "processing", ex=SETTINGS.dedupe_lease_s)
    await redis.aclose()
    started = time.monotonic()
    processed_at: list[float] = []

    async def process(e: Envelope, writer: StreamWriter) -> None:
        processed_at.append(time.monotonic() - started)
        await writer.done()

    await _produce(req, [env.model_dump_json().encode()])
    await _run_worker(req, dlq, process, expected_commits=1)
    assert len(processed_at) == 1
    assert processed_at[0] >= SETTINGS.dedupe_lease_s - 0.5
    assert [e["type"] for e in await _events(env.thread_id, env.message_id)] == ["done"]


async def test_dlq_replay_reprocesses_a_dependency_failure(topics) -> None:
    """A message dead-lettered for a dependency fault is replayed with the DLQ tool and completes.

    The failed attempt left `ErrorEvent{dependency_unavailable}` in its stream; replay removes that
    stream, else the worker would read it as an outcome already recorded and dead-letter it again.
    """
    req, dlq = topics
    env = Envelope(message_id=uuid.uuid4(), thread_id="t-it", user_id="u", text="hi", trace_id="t")
    group = f"it-{uuid.uuid4().hex[:8]}"

    async def failing(e: Envelope, w: StreamWriter) -> None:
        raise TransientDependencyError("qdrant down")

    async def ok(e: Envelope, w: StreamWriter) -> None:
        await w.token("back")
        await w.done()

    await _produce(req, [env.model_dump_json().encode()])
    await _run_worker(req, dlq, failing, 1, group)
    (record,) = dlq_tool.read(KAFKA, dlq)
    assert (record.record["reason"], record.envelope) == ("dependency_unavailable", env)

    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    sync = redis_sync.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        assert dlq_tool.replay([record], bootstrap=KAFKA, topic=req, redis=sync) == [
            str(env.message_id)
        ]
        await _run_worker(req, dlq, ok, 2, group)
        assert [(e["type"], e["seq"]) for e in await _events("t-it", env.message_id)] == [
            ("token", 0),
            ("done", 1),
        ]
        assert await redis.get(f"dedupe:{env.message_id}") == "done"
        assert len(dlq_tool.read(KAFKA, dlq)) == 1  # not dead-lettered again
    finally:
        sync.close()
        await redis.aclose()
