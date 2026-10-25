"""Worker.handle outcomes with Kafka and Redis test doubles (no broker)."""

import hashlib
import json
import logging
from typing import Any
from uuid import uuid4

from chat_common.schemas import Envelope
from chat_common.settings import Settings
from fakes import FakeRedis
from worker.consumer import TransientDependencyError, Worker
from worker.lease import Lease
from worker.streams import StreamWriter

SETTINGS = Settings(
    inference_timeout_s=1, max_retries=2, retry_backoff_max_s=0.01, dedupe_lease_s=5
)


class FakeMsg:
    """Test double for confluent_kafka.Message."""

    def __init__(self, value: bytes, key: bytes = b"t1") -> None:
        """Hold value and key."""
        self._v, self._k = value, key

    def value(self) -> bytes:
        """Payload."""
        return self._v

    def key(self) -> bytes:
        """Key."""
        return self._k

    def error(self) -> None:
        """No error."""


class FakeConsumer:
    """Records commits."""

    def __init__(self) -> None:
        """No commits yet."""
        self.commits: list[Any] = []

    async def commit(self, message: Any, asynchronous: bool) -> None:
        """Record a synchronous commit."""
        assert asynchronous is False
        self.commits.append(message)


class FakeDLQ:
    """Records DLQ records; produce returns an awaitable delivery like AIOProducer."""

    def __init__(self) -> None:
        """Empty DLQ."""
        self.records: list[dict[str, Any]] = []

    async def produce(self, topic: str, value: bytes, key: bytes | None) -> Any:
        """Record and return a completed delivery future."""
        self.records.append({"topic": topic, **json.loads(value)})

        async def _delivered() -> None:
            return None

        return _delivered()


def _env() -> Envelope:
    """A valid envelope."""
    return Envelope(message_id=uuid4(), thread_id="t1", user_id="u", text="hi", trace_id="x")


def _worker(process) -> tuple[Worker, FakeConsumer, FakeDLQ, FakeRedis]:
    """Worker over doubles."""
    consumer, dlq, redis = FakeConsumer(), FakeDLQ(), FakeRedis()
    w = Worker(SETTINGS, consumer=consumer, dlq_producer=dlq, redis=redis, process=process)
    return w, consumer, dlq, redis


async def _crashed_writer(redis: FakeRedis, env: Envelope) -> StreamWriter:
    """The writer of an earlier attempt, holding its lease (delete it to model the crash expiry)."""
    lease = Lease(f"dedupe:{env.message_id}")
    await redis.set(lease.key, lease.token)
    return StreamWriter(redis, "t1", env.message_id, lease=lease, maxlen=100, ttl_s=60)


async def _ok(env: Envelope, writer: StreamWriter) -> None:
    """Processor double: two tokens then done."""
    await writer.token("a")
    await writer.token("b")
    await writer.done()


async def test_malformed_goes_to_dlq_without_processing() -> None:
    """A4 unit: not an envelope ⇒ DLQ + commit, processor never called."""
    calls = []
    w, consumer, dlq, _ = _worker(lambda e, wr: calls.append(e))
    msg = FakeMsg(b"not json")
    await w.handle(msg)
    assert dlq.records[0]["reason"] == "malformed" and calls == []
    assert consumer.commits == [msg]


async def test_success_marks_done_and_duplicate_is_skipped() -> None:
    """A5 unit: second delivery of the same message is committed without reprocessing."""
    calls: list[str] = []

    async def process(env: Envelope, writer: StreamWriter) -> None:
        calls.append(str(env.message_id))
        await _ok(env, writer)

    w, consumer, _, redis = _worker(process)
    env = _env()
    msg = FakeMsg(env.model_dump_json().encode())
    await w.handle(msg)
    await w.handle(msg)
    assert len(calls) == 1 and len(consumer.commits) == 2
    assert await redis.get(f"dedupe:{env.message_id}") == "done"
    events = [
        json.loads(f["data"])["type"] for _, f in redis.streams[f"tokens:t1:{env.message_id}"]
    ]
    assert events == ["token", "token", "done"]


async def test_transient_failure_retries_then_succeeds() -> None:
    """Two transient faults then success: three attempts, one done."""
    attempts = []

    async def flaky(env: Envelope, writer: StreamWriter) -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise TransientDependencyError("qdrant down")
        await _ok(env, writer)

    w, _, dlq, _ = _worker(flaky)
    await w.handle(FakeMsg(_env().model_dump_json().encode()))
    assert len(attempts) == 3 and dlq.records == []


async def test_exhausted_retries_error_event_dlq_and_lease_released() -> None:
    """Persistent fault ⇒ ErrorEvent{dependency_unavailable}, DLQ record, dedupe key deleted."""

    async def down(env: Envelope, writer: StreamWriter) -> None:
        raise TransientDependencyError("llm down")

    w, consumer, dlq, redis = _worker(down)
    env = _env()
    await w.handle(FakeMsg(env.model_dump_json().encode()))
    (row,) = redis.streams[f"tokens:t1:{env.message_id}"]
    assert json.loads(row[1]["data"]) == {
        "type": "error",
        "seq": 0,
        "code": "dependency_unavailable",
        "detail": "a dependency is unavailable; try again",
    }
    assert dlq.records[0]["reason"] == "dependency_unavailable"
    assert dlq.records[0]["envelope"]["message_id"] == str(env.message_id)
    assert await redis.get(f"dedupe:{env.message_id}") is None
    assert len(consumer.commits) == 1


async def test_no_retry_after_tokens_were_streamed() -> None:
    """A fault after output started is final (a retry would duplicate tokens)."""
    attempts = []

    async def partial(env: Envelope, writer: StreamWriter) -> None:
        attempts.append(1)
        await writer.token("half")
        raise TransientDependencyError("llm dropped")

    w, _, dlq, _ = _worker(partial)
    await w.handle(FakeMsg(_env().model_dump_json().encode()))
    assert len(attempts) == 1 and dlq.records[0]["reason"] == "dependency_unavailable"


async def test_redelivery_after_streamed_done_keeps_stream_and_skips_processing() -> None:
    """`done` was written but the worker died before marking dedupe: no rerun, stream kept."""
    calls = []
    w, consumer, dlq, redis = _worker(lambda e, wr: calls.append(e))
    env = _env()
    writer = await _crashed_writer(redis, env)
    await _ok(env, writer)
    await redis.delete(writer.lease.key)
    before = list(redis.streams[f"tokens:t1:{env.message_id}"])
    await w.handle(FakeMsg(env.model_dump_json().encode()))
    assert calls == [] and dlq.records == [] and len(consumer.commits) == 1
    assert redis.streams[f"tokens:t1:{env.message_id}"] == before
    assert await redis.get(f"dedupe:{env.message_id}") == "done"


async def test_redelivery_after_streamed_dependency_error_still_reaches_dlq() -> None:
    """The ErrorEvent was written but the DLQ record was not: write it, release, commit."""
    calls = []
    w, consumer, dlq, redis = _worker(lambda e, wr: calls.append(e))
    env = _env()
    writer = await _crashed_writer(redis, env)
    await writer.error("dependency_unavailable", "a dependency is unavailable; try again")
    await redis.delete(writer.lease.key)
    await w.handle(FakeMsg(env.model_dump_json().encode()))
    assert calls == [] and dlq.records[0]["reason"] == "dependency_unavailable"
    assert await redis.get(f"dedupe:{env.message_id}") is None
    assert len(consumer.commits) == 1


async def test_malformed_input_is_never_stored_or_logged(caplog) -> None:
    """A malformed record skipped the gateway's scrubber, so its bytes may hold raw PII.

    The DLQ keeps only its digest, length and the validation error types and locations.
    """
    raw = b'{"text": "card 4111111111111111 for jane.doe@example.com"'
    w, consumer, dlq, _ = _worker(lambda e, wr: None)
    with caplog.at_level(logging.DEBUG):
        await w.handle(FakeMsg(raw))
    (record,) = dlq.records
    stored = json.dumps(record) + caplog.text
    for secret in ("4111111111111111", "jane.doe@example.com", "card"):
        assert secret not in stored, secret
    assert record["reason"] == "malformed" and record["length"] == len(raw)
    assert record["sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(consumer.commits) == 1


async def test_status_note_does_not_block_a_retry() -> None:
    """A status event is not reply output: a transient fault after it is still retried.

    The retried attempt does not repeat the note, and the stream stays seq-contiguous.
    """
    attempts = []

    async def flaky(env: Envelope, writer: StreamWriter) -> None:
        attempts.append(1)
        await writer.status("searching your documents")
        if len(attempts) == 1:
            raise TransientDependencyError("rag-api 503")
        await _ok(env, writer)

    w, _, dlq, redis = _worker(flaky)
    env = _env()
    await w.handle(FakeMsg(env.model_dump_json().encode()))
    events = [json.loads(f["data"]) for _, f in redis.streams[f"tokens:t1:{env.message_id}"]]
    assert len(attempts) == 2 and dlq.records == []
    assert [e["type"] for e in events] == ["status", "token", "token", "done"]
    assert [e["seq"] for e in events] == [0, 1, 2, 3]


async def test_no_retry_after_the_first_token_even_if_a_status_came_first() -> None:
    """The no-retry-after-first-token rule is unchanged by the status event."""
    attempts = []

    async def partial(env: Envelope, writer: StreamWriter) -> None:
        attempts.append(1)
        await writer.status("searching your documents")
        await writer.token("half")
        raise TransientDependencyError("llm dropped")

    w, _, dlq, _ = _worker(partial)
    await w.handle(FakeMsg(_env().model_dump_json().encode()))
    assert len(attempts) == 1 and dlq.records[0]["reason"] == "dependency_unavailable"


async def test_redelivery_after_a_crash_following_a_status_note_reprocesses() -> None:
    """`status` and `citations` are not terminal: a stream ending in one is a partial attempt.

    Treating any non-token event as terminal would skip the work and leave the client waiting.
    """
    calls = []

    async def process(env: Envelope, writer: StreamWriter) -> None:
        calls.append(1)
        await _ok(env, writer)

    w, consumer, _, redis = _worker(process)
    env = _env()
    writer = await _crashed_writer(redis, env)
    await writer.status("searching your documents")
    await redis.delete(writer.lease.key)
    await w.handle(FakeMsg(env.model_dump_json().encode()))
    events = [
        json.loads(f["data"])["type"] for _, f in redis.streams[f"tokens:t1:{env.message_id}"]
    ]
    assert calls == [1] and events == ["token", "token", "done"] and len(consumer.commits) == 1
