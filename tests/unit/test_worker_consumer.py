"""Worker.handle outcomes with Kafka and Redis test doubles (no broker)."""

import json
from typing import Any
from uuid import uuid4

from chat_common.schemas import Envelope
from chat_common.settings import Settings
from fakes import FakeRedis
from worker.consumer import TransientDependencyError, Worker
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
