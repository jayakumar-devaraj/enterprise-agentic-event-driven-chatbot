"""Kafka consumer loop: dedupe lease, bounded retries, DLQ, manual commit (SPEC.md §4, §5, §7)."""

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from chat_common.schemas import Envelope
from chat_common.settings import Settings
from pydantic import ValidationError
from redis.exceptions import RedisError

from worker.lease import Lease, LeaseLost
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
        failing, the dedupe lease lost to another attempt) propagates and stops the process without
        committing, so the record is redelivered after restart instead of being skipped.
        """
        await self._consumer.subscribe([topic])
        while not stop.is_set():
            msg = await self._consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                log.warning("kafka poll error", extra={"error": str(msg.error())})
                continue
            try:
                await self.handle(msg)
            except LeaseLost as exc:
                log.error("dedupe lease lost; stopping without commit", extra={"lease": str(exc)})
                raise

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
            await self._commit(msg)
            return

        writer = StreamWriter(
            self._redis,
            env.thread_id,
            env.message_id,
            lease=lease,
            maxlen=self._s.stream_maxlen,
            ttl_s=self._s.stream_ttl_s,
        )
        last = await writer.last_event()
        if last is not None and last["type"] != "token":
            # An earlier attempt wrote its terminal event and died before recording the outcome:
            # keep the stream the client may be reading and finish the bookkeeping only.
            log.info("terminal event already streamed; recording outcome", extra=ctx)
            if last.get("code") == "dependency_unavailable":
                await lease.check(self._redis)
                await self._to_dlq(msg, "dependency_unavailable", "recovered after restart", env)
                await lease.release(self._redis)
            else:
                await lease.done(self._redis, self._s.dedupe_ttl_s)
            await self._commit(msg)
            return
        await writer.reset()  # partial attempt: the retry restarts the stream at seq 0 (SPEC §6)
        error = await self._run_with_retries(env, writer, ctx)
        if error is None:
            try:
                await lease.done(self._redis, self._s.dedupe_ttl_s)
            except RedisError:
                # The DoneEvent is already durable; the lease simply expires.
                log.error("could not mark message done", extra=ctx)
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
