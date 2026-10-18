"""Kafka consumer loop: dedupe lease, bounded retries, DLQ, manual commit (SPEC.md §4, §5, §7)."""

import asyncio
import base64
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from chat_common.schemas import Envelope
from chat_common.settings import Settings
from pydantic import ValidationError
from redis.exceptions import RedisError

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


class Worker:
    """Processes one Kafka record at a time per consumer, committing only durable outcomes."""

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

    async def run(self, topic: str, stop: asyncio.Event) -> None:
        """Poll until `stop` is set.

        Anything that prevents a durable outcome (Redis down while taking the lease, DLQ write
        failing) propagates and stops the process without committing, so the record is redelivered
        after restart instead of being skipped.
        """
        await self._consumer.subscribe([topic])
        while not stop.is_set():
            msg = await self._consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                log.warning("kafka poll error", extra={"error": str(msg.error())})
                continue
            await self.handle(msg)

    async def _commit(self, msg: _Message) -> None:
        """Synchronous commit of this record's offset."""
        await self._consumer.commit(message=msg, asynchronous=False)

    async def _to_dlq(self, msg: _Message, reason: str, error: str, env: Envelope | None) -> None:
        """Write the DLQ record and wait for the broker ack (durable outcome)."""
        record: dict[str, Any] = {
            "reason": reason,
            "error": error,
            "ts": datetime.now(UTC).isoformat(),
        }
        if env is not None:
            record["envelope"] = json.loads(env.model_dump_json())
        else:
            record["original_b64"] = base64.b64encode(msg.value() or b"").decode()
        delivery = await self._dlq.produce(
            self._dlq_topic, value=json.dumps(record).encode(), key=msg.key()
        )
        await delivery

    async def _acquire_lease(self, env: Envelope) -> bool:
        """True when this worker owns the message; False when it is already done.

        A live `processing` lease means a previous holder may still finish or has crashed: wait
        (blocking this partition, preserving per-thread order) until it turns `done` or expires.
        """
        key = f"dedupe:{env.message_id}"
        while True:
            if await self._redis.set(key, "processing", nx=True, ex=self._s.dedupe_lease_s):
                return True
            state = await self._redis.get(key)
            if state == "done":
                return False
            await asyncio.sleep(1.0)

    async def handle(self, msg: _Message) -> None:
        """One record → exactly one durable outcome, then commit."""
        try:
            env = Envelope.model_validate_json(msg.value() or b"")
        except ValidationError as exc:
            log.warning("malformed envelope to DLQ", extra={"error": str(exc)[:500]})
            await self._to_dlq(msg, "malformed", str(exc)[:2000], None)
            await self._commit(msg)
            return

        ctx = {
            "thread_id": env.thread_id,
            "message_id": str(env.message_id),
            "trace_id": env.trace_id,
        }
        if not await self._acquire_lease(env):
            log.info("duplicate delivery skipped", extra=ctx)
            await self._commit(msg)
            return

        writer = StreamWriter(
            self._redis,
            env.thread_id,
            env.message_id,
            maxlen=self._s.stream_maxlen,
            ttl_s=self._s.stream_ttl_s,
        )
        last = await writer.last_event()
        if last is not None and last["type"] != "token":
            # An earlier attempt wrote its terminal event and died before recording the outcome:
            # keep the stream the client may be reading and finish the bookkeeping only.
            log.info("terminal event already streamed; recording outcome", extra=ctx)
            if last.get("code") == "dependency_unavailable":
                await self._to_dlq(msg, "dependency_unavailable", "recovered after restart", env)
                await self._redis.delete(f"dedupe:{env.message_id}")
            else:
                await self._redis.set(f"dedupe:{env.message_id}", "done", ex=self._s.dedupe_ttl_s)
            await self._commit(msg)
            return
        await writer.reset()  # partial attempt: the retry restarts the stream at seq 0 (SPEC §6)
        error = await self._run_with_retries(env, writer, ctx)
        if error is None:
            try:
                await self._redis.set(f"dedupe:{env.message_id}", "done", ex=self._s.dedupe_ttl_s)
            except RedisError:
                # The DoneEvent is already durable; the lease simply expires.
                log.error("could not mark message done", extra=ctx)
            await self._commit(msg)
            return

        try:
            await writer.error("dependency_unavailable", "a dependency is unavailable; try again")
        except RedisError:
            log.error("could not write ErrorEvent", extra=ctx)
        await self._to_dlq(msg, "dependency_unavailable", error, env)
        try:
            await self._redis.delete(f"dedupe:{env.message_id}")
        except RedisError:
            log.error("could not release dedupe lease; it will expire", extra=ctx)
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
                if writer.seq > 0 or attempt == self._s.max_retries:
                    break
                await asyncio.sleep(min(0.5 * 2**attempt, self._s.retry_backoff_max_s))
        return last
