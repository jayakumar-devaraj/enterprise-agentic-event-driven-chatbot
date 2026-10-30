"""Worker.run with several records in flight: per-thread order, bounded concurrency, commits."""

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from chat_common.schemas import Envelope
from chat_common.settings import Settings
from fakes import FakeRedis
from worker.consumer import Worker
from worker.streams import StreamWriter

SETTINGS = Settings(
    inference_timeout_s=5,
    max_retries=0,
    retry_backoff_max_s=0.01,
    dedupe_lease_s=10,
    worker_concurrency=4,
)


class Rec:
    """Test double for a polled confluent_kafka.Message on partition 0."""

    def __init__(self, env: Envelope, offset: int) -> None:
        """Serialize the envelope; the key is the thread id, as the gateway sets it."""
        self.env, self._offset = env, offset

    def value(self) -> bytes:
        """Payload."""
        return self.env.model_dump_json().encode()

    def key(self) -> bytes:
        """Key."""
        return self.env.thread_id.encode()

    def error(self) -> None:
        """No error."""

    def topic(self) -> str:
        """Topic."""
        return "chat-requests"

    def partition(self) -> int:
        """Partition."""
        return 0

    def offset(self) -> int:
        """Offset."""
        return self._offset


class Kafka:
    """Test double consumer: polls `records` in order, then nothing; records committed offsets."""

    def __init__(self, records: list[Rec]) -> None:
        """Queue the records."""
        self.records, self.commits = list(records), []

    async def subscribe(self, topics: list[str]) -> None:
        """Nothing to do."""

    async def poll(self, timeout: float) -> Rec | None:  # noqa: ASYNC109 (confluent's signature)
        """Next record, or None after a short wait."""
        if self.records:
            return self.records.pop(0)
        await asyncio.sleep(0.01)
        return None

    async def commit(self, offsets: list[Any], asynchronous: bool) -> None:
        """Record the committed next offset per partition."""
        assert asynchronous is False
        self.commits += [(tp.partition, tp.offset) for tp in offsets]


def _recs(*threads: str) -> list[Rec]:
    """One record per thread name, at consecutive offsets."""
    return [
        Rec(Envelope(message_id=uuid4(), thread_id=t, user_id="u", text=t, trace_id="x"), i)
        for i, t in enumerate(threads)
    ]


class Gated:
    """Processor double: each message runs until its gate opens; records start and end order."""

    def __init__(self) -> None:
        """No gates yet."""
        self.gates: dict[str, asyncio.Event] = {}
        self.started: list[str] = []
        self.finished: list[str] = []
        self.running = self.peak = 0

    def gate(self, rec: Rec) -> asyncio.Event:
        """The gate for a record."""
        return self.gates.setdefault(str(rec.env.message_id), asyncio.Event())

    async def __call__(self, env: Envelope, writer: StreamWriter) -> None:
        """Wait for the gate, then finish with `done`."""
        self.started.append(str(env.message_id))
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            await self.gates.setdefault(str(env.message_id), asyncio.Event()).wait()
            await writer.done()
        finally:
            self.running -= 1
        self.finished.append(str(env.message_id))


async def _until(cond) -> None:
    """Wait up to 2 s until `cond()` holds (polled: the conditions span several doubles)."""
    async with asyncio.timeout(2):
        while not cond():  # noqa: ASYNC110
            await asyncio.sleep(0.005)


def _start(records: list[Rec], process: Any) -> tuple[Kafka, asyncio.Event, asyncio.Task]:
    kafka, stop = Kafka(records), asyncio.Event()
    w = Worker(SETTINGS, consumer=kafka, dlq_producer=None, redis=FakeRedis(), process=process)
    return kafka, stop, asyncio.create_task(w.run("chat-requests", stop))


def _id(rec: Rec) -> str:
    return str(rec.env.message_id)


async def test_other_threads_run_while_one_thread_waits_its_turn() -> None:
    a1, b1, a2 = recs = _recs("a", "b", "a")
    p = Gated()
    _, stop, task = _start(recs, p)
    await _until(lambda: p.started == [_id(a1), _id(b1)])
    await asyncio.sleep(0.05)
    assert _id(a2) not in p.started  # a2 waits for a1, b1 does not
    p.gate(a1).set()
    await _until(lambda: _id(a2) in p.started)
    p.gate(a2).set()
    p.gate(b1).set()
    await _until(lambda: len(p.finished) == 3)
    assert p.finished.index(_id(a1)) < p.finished.index(_id(a2))
    stop.set()
    await task


async def test_offsets_are_committed_only_below_the_oldest_unfinished_record() -> None:
    """Committing offset n commits every record below it, so a fast later record must wait."""
    slow, fast = recs = _recs("a", "b")
    p = Gated()
    kafka, stop, task = _start(recs, p)
    await _until(lambda: len(p.started) == 2)
    p.gate(fast).set()
    await _until(lambda: _id(fast) in p.finished)
    await asyncio.sleep(0.05)
    assert all(offset <= 0 for _, offset in kafka.commits)
    p.gate(slow).set()
    await _until(lambda: (0, 2) in kafka.commits)
    stop.set()
    await task


async def test_in_flight_records_are_bounded_by_worker_concurrency() -> None:
    recs = _recs(*"abcdef")
    p = Gated()
    _, stop, task = _start(recs, p)
    await _until(lambda: p.running == SETTINGS.worker_concurrency)
    await asyncio.sleep(0.05)
    assert p.peak == SETTINGS.worker_concurrency
    for r in recs:
        p.gate(r).set()
    await _until(lambda: len(p.finished) == len(recs))
    stop.set()
    await task


async def test_a_failed_record_stops_its_thread_and_the_process_without_commit() -> None:
    """A later message of the thread must never run ahead of one that did not finish."""
    a1, a2 = recs = _recs("a", "a")
    calls: list[str] = []

    async def process(env: Envelope, writer: StreamWriter) -> None:
        calls.append(str(env.message_id))
        raise RuntimeError("bug")

    kafka, stop, task = _start(recs, process)
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(task, 2)
    assert calls == [_id(a1)] and kafka.commits == []


async def test_stop_waits_for_in_flight_records_and_commits_them() -> None:
    (a1,) = recs = _recs("a")
    p = Gated()
    kafka, stop, task = _start(recs, p)
    await _until(lambda: len(p.started) == 1)
    stop.set()
    await asyncio.sleep(0.05)
    assert not task.done()
    p.gate(a1).set()
    await asyncio.wait_for(task, 2)
    assert kafka.commits == [(0, 1)]
