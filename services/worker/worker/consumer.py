"""Kafka consumer loop: dedupe lease, bounded retries, DLQ, manual commit (SPEC.md §4, §5, §7)."""

import asyncio
import hashlib
import json
import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from chat_common.schemas import TERMINAL_TYPES, Envelope
from chat_common.settings import Settings
from confluent_kafka import TopicPartition
from pydantic import ValidationError
from redis.exceptions import RedisError

from worker.lease import Lease, LeaseLost
from worker.metrics import IN_FLIGHT, QUEUE_DELAY, RECORD_SECONDS, RECORDS, RETRIES
from worker.diag import diag
from worker.streams import StreamWriter

log = logging.getLogger("worker.consumer")


class TransientDependencyError(Exception):
    """Qdrant, LLM or Redis fault that may succeed on retry."""


Processor = Callable[[Envelope, StreamWriter], Awaitable[None]]
"""Runs the pipeline for one envelope and writes its events (token…done / approval_required)."""


class _Message(Protocol):
    """The subset of confluent_kafka.Message the worker reads."""

    def value(self) -> bytes | None: ...

    def key(self) -> bytes | None: ...

    def error(self) -> Any: ...


class _Offsets:
    """Per-partition commit point: below the oldest polled record whose outcome is not durable.

    Committing offset n commits every record below n, so a later record that finishes first must
    not move the commit point past an earlier one still running (SPEC §5).
    """

    def __init__(self) -> None:
        """Nothing polled yet."""
        self._pending: dict[tuple[str, int], Counter[int]] = {}
        self._next: dict[tuple[str, int], int] = {}
        self._committed: dict[tuple[str, int], int] = {}
        self._tracked: dict[int, Any] = {}  # holds each record, so its id() stays unique

    def add(self, msg: Any) -> None:
        """Track a polled record until its outcome is durable."""
        self._tracked[id(msg)] = msg
        self._pending.setdefault((msg.topic(), msg.partition()), Counter())[msg.offset()] += 1

    def tracks(self, msg: Any) -> bool:
        """True for a record polled by `Worker.run` and not yet finished."""
        return id(msg) in self._tracked

    def finish(self, msg: Any) -> TopicPartition | None:
        """Mark the record durable; the offset to commit if the commit point moved, else None."""
        del self._tracked[id(msg)]
        tp = (msg.topic(), msg.partition())
        pending = self._pending[tp]
        pending[msg.offset()] -= 1
        if pending[msg.offset()] == 0:
            del pending[msg.offset()]
        self._next[tp] = max(self._next.get(tp, 0), msg.offset() + 1)
        point = min(pending) if pending else self._next[tp]
        if point <= self._committed.get(tp, -1):
            return None
        self._committed[tp] = point
        return TopicPartition(tp[0], tp[1], point)


class Worker:
    """Processes Kafka records, several threads at once, committing only durable outcomes.

    Records of one thread (the record key) run one at a time in offset order; records of other
    threads run alongside, up to WORKER_CONCURRENCY per consumer.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        consumer: Any,
        dlq_producer: Any,
        redis: Any,
        process: Processor,
        dlq_topic: str | None = None,
    ) -> None:
        """Wire collaborators; `consumer`/`dlq_producer` are confluent_kafka.aio clients."""
        self._s = settings
        self._consumer = consumer
        self._dlq = dlq_producer
        self._redis = redis
        self._process = process
        self._dlq_topic = dlq_topic or settings.kafka_dlq_topic
        self._offsets = _Offsets()
        self._commit_lock = asyncio.Lock()
        self._lanes: dict[bytes | None, asyncio.Task[None]] = {}

    async def run(self, topic: str, stop: asyncio.Event) -> None:
        """Poll until `stop` is set, then wait for the records in flight.

        Anything that prevents a durable outcome (Redis down while taking the lease, DLQ write
        failing, the dedupe lease lost to another attempt) propagates and stops the process without
        committing, so the record is redelivered after restart instead of being skipped. The other
        records in flight are cancelled, as in a crash, and redelivered too.
        """
        await self._consumer.subscribe([topic])
        slots = asyncio.Semaphore(self._s.worker_concurrency)
        tasks: set[asyncio.Task[None]] = set()
        try:
            while not stop.is_set():
                await slots.acquire()
                for task in [t for t in tasks if t.done()]:
                    tasks.discard(task)
                    task.result()  # a failed record stops the process here
                msg = await self._consumer.poll(1.0)
                if msg is None or msg.error():
                    if msg is not None:
                        log.warning("kafka poll error", extra={"error": str(msg.error())})
                    slots.release()
                    continue
                self._offsets.add(msg)
                key = msg.key()
                task = asyncio.create_task(self._lane(self._lanes.get(key), msg, slots))
                self._lanes[key] = task
                tasks.add(task)
            for task in asyncio.as_completed(tasks):
                await task
        except LeaseLost as exc:
            log.error("dedupe lease lost; stopping without commit", extra={"lease": str(exc)})
            raise
        finally:
            for task in tasks:
                task.cancel()

    async def _lane(
        self, previous: asyncio.Task[None] | None, msg: _Message, slots: asyncio.Semaphore
    ) -> None:
        """Handle `msg` after the thread's previous record; never after one that failed."""
        try:
            if previous is not None:
                await asyncio.wait([previous])
                if previous.cancelled() or previous.exception() is not None:
                    return
            with IN_FLIGHT.track_inprogress(), RECORD_SECONDS.time():
                await self.handle(msg)
        finally:
            slots.release()
            if self._lanes.get(msg.key()) is asyncio.current_task():
                del self._lanes[msg.key()]

    async def _commit(self, msg: _Message) -> None:
        """Synchronous commit up to the partition's commit point (a record handled outside `run`
        commits its own offset)."""
        if not self._offsets.tracks(msg):
            await self._consumer.commit(message=msg, asynchronous=False)
            return
        async with self._commit_lock:
            point = self._offsets.finish(msg)
            if point is not None:
                await self._consumer.commit(offsets=[point], asynchronous=False)

    async def _to_dlq(self, msg: _Message, reason: str, error: str, env: Envelope | None) -> None:
        """Write the DLQ record and wait for the broker ack (durable outcome)."""
        record: dict[str, Any] = {
            "reason": reason,
            "error": error,
            "ts": datetime.now(UTC).isoformat(),
        }
        if env is not None:
            record["envelope"] = json.loads(env.model_dump_json())  # scrubbed by the gateway
        else:
            raw = msg.value() or b""  # unscrubbed: identify it, never keep it
            record["sha256"] = hashlib.sha256(raw).hexdigest()
            record["length"] = len(raw)
        delivery = await self._dlq.produce(
            self._dlq_topic, value=json.dumps(record).encode(), key=msg.key()
        )
        await delivery

    async def _acquire_lease(self, env: Envelope) -> Lease | None:
        """This attempt's lease when it owns the message; None when the message is already done.

        A live `processing:<token>` lease means a previous holder may still finish or has crashed:
        wait (blocking this partition, preserving per-thread order) until it turns `done` or
        expires. Writes made under the lease are fenced by its token (ADR 0009).
        """
        lease = Lease(f"dedupe:{env.message_id}")
        while True:
            if await self._redis.set(lease.key, lease.token, nx=True, ex=self._s.dedupe_lease_s):
                return lease
            state = await self._redis.get(lease.key)
            if state == "done":
                return None
            await asyncio.sleep(1.0)

    async def handle(self, msg: _Message) -> None:
        """One record → exactly one durable outcome, then commit."""
        try:
            env = Envelope.model_validate_json(msg.value() or b"")
        except ValidationError as exc:
            # These bytes skipped the gateway's scrubber and may hold raw PII, so neither the log
            # nor the DLQ keeps any of them, only error types and Envelope field names (SPEC §7).
            fields = set(Envelope.model_fields)
            problems = json.dumps(
                [
                    {
                        "type": e["type"],
                        "loc": [p if p in fields or isinstance(p, int) else "?" for p in e["loc"]],
                    }
                    for e in exc.errors(include_url=False, include_input=False)
                ]
            )
            log.warning("malformed envelope to DLQ", extra={"error": problems[:500]})
            await self._to_dlq(msg, "malformed", problems[:2000], None)
            RECORDS.labels("dlq_malformed").inc()
            await self._commit(msg)
            return

        ctx = {
            "thread_id": env.thread_id,
            "message_id": str(env.message_id),
            "trace_id": env.trace_id,
        }
        lease = await self._acquire_lease(env)
        if lease is None:
            log.info("duplicate delivery skipped", extra=ctx)
            await diag(self._redis, env.thread_id, "dup_skip", text=env.text)
            RECORDS.labels("duplicate").inc()
            await self._commit(msg)
            return
        QUEUE_DELAY.observe((datetime.now(UTC) - env.ts).total_seconds())

        await diag(self._redis, env.thread_id, 'lease', text=env.text, token=lease.token[-6:])
        writer = StreamWriter(
            self._redis,
            env.thread_id,
            env.message_id,
            lease=lease,
            maxlen=self._s.stream_maxlen,
            ttl_s=self._s.stream_ttl_s,
        )
        last = await writer.last_event()
        if last is not None and last["type"] in TERMINAL_TYPES:
            # An earlier attempt wrote its terminal event and died before recording the outcome:
            # keep the stream the client may be reading and finish the bookkeeping only.
            log.info("terminal event already streamed; recording outcome", extra=ctx)
            await diag(self._redis, env.thread_id, "terminal_seen", text=env.text, last=last)
            if last.get("code") == "dependency_unavailable":
                await lease.check(self._redis)
                await self._to_dlq(msg, "dependency_unavailable", "recovered after restart", env)
                await lease.release(self._redis)
                RECORDS.labels("dlq_dependency").inc()
            else:
                await lease.done(self._redis, self._s.dedupe_ttl_s)
                RECORDS.labels("done").inc()
            await self._commit(msg)
            return
        await writer.reset()  # partial attempt: the retry restarts the stream at seq 0 (SPEC §6)
        error = await self._run_with_retries(env, writer, ctx)
        await diag(self._redis, env.thread_id, 'ran', text=env.text, error=error)
        if error is None:
            try:
                await lease.done(self._redis, self._s.dedupe_ttl_s)
            except RedisError:
                # The DoneEvent is already durable; the lease simply expires.
                log.error("could not mark message done", extra=ctx)
            RECORDS.labels("done").inc()
            await self._commit(msg)
            return

        try:
            await writer.error("dependency_unavailable", "a dependency is unavailable; try again")
        except RedisError:
            log.error("could not write ErrorEvent", extra=ctx)
        await lease.check(self._redis)
        await self._to_dlq(msg, "dependency_unavailable", error, env)
        try:
            await lease.release(self._redis)
        except RedisError:
            log.error("could not release dedupe lease; it will expire", extra=ctx)
        RECORDS.labels("dlq_dependency").inc()
        await self._commit(msg)

    async def _run_with_retries(
        self, env: Envelope, writer: StreamWriter, ctx: dict[str, str]
    ) -> str | None:
        """Retry transient faults with capped exponential backoff while nothing was streamed.

        Once a token has been written a retry would duplicate output, so a later fault is final.
        Returns None on success, else the last error text.
        """
        last = ""
        for attempt in range(self._s.max_retries + 1):
            try:
                async with asyncio.timeout(self._s.inference_timeout_s):
                    await self._process(env, writer)
                return None
            except (TransientDependencyError, RedisError, TimeoutError, ConnectionError) as exc:
                last = f"{type(exc).__name__}: {exc}"
                log.warning("transient failure", extra={**ctx, "attempt": attempt, "error": last})
                await diag(self._redis, env.thread_id, "transient", text=env.text, attempt=attempt, error=last, started=writer.reply_started)
                if writer.reply_started or attempt == self._s.max_retries:
                    break
                RETRIES.inc()
                await asyncio.sleep(min(0.5 * 2**attempt, self._s.retry_backoff_max_s))
        return last
