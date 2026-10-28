"""Async Kafka producer for chat envelopes (confluent-kafka asyncio, ADR 0003).

`AIOProducer` runs unbatched with a delivery-report task (ADR 0003 addendum 2026-10-24). By
default, its Python-side buffer flushes only after `buffer_timeout` of inactivity, and acks surface
only on a later poll. That set a ~2 s floor on every send, and probably caused the timeouts (503)
seen while requests kept arriving (`docs/reports/latency-2026-10-24.md`).
"""

import asyncio
import contextlib
from typing import Protocol

from chat_common.schemas import Envelope
from confluent_kafka import KafkaException
from confluent_kafka.aio import AIOProducer
from opentelemetry import trace

tracer = trace.get_tracer("gateway")


class DependencyUnavailable(Exception):
    """An infrastructure dependency failed; the API maps this to 503."""


def envelope_bytes(envelope: Envelope) -> bytes:
    """The Kafka value. `tenant_id` is left out when unset, so a worker that predates the field
    (`extra="forbid"`) still accepts records from users who have no tenant."""
    return envelope.model_dump_json(
        exclude={"tenant_id"} if envelope.tenant_id is None else None
    ).encode()


class EnvelopeSink(Protocol):
    """What the app needs from a producer (lets tests use a double)."""

    async def send(self, envelope: Envelope) -> None: ...

    async def ready(self) -> None: ...

    async def close(self) -> None: ...


class KafkaEnvelopeProducer:
    """Produces keyed by `thread_id` and waits for the broker's delivery report.

    `batch_size=1` hands each record to librdkafka at once, and `buffer_timeout=0` turns off the
    inactivity flush, which is then unused. Delivery reports arrive only through `poll()`, so a
    task keeps polling. Without it, sends never resolve.
    """

    def __init__(self, bootstrap: str, topic: str, timeout_s: float = 5.0) -> None:
        """Idempotent delivery with a bounded delivery timeout; needs a running event loop."""
        self._topic = topic
        self._timeout_s = timeout_s
        self._producer = AIOProducer(
            {
                "bootstrap.servers": bootstrap,
                "enable.idempotence": True,
                "message.timeout.ms": int(timeout_s * 1000),
            },
            batch_size=1,
            buffer_timeout=0,
        )
        self._reports = asyncio.get_running_loop().create_task(self._serve_reports())

    async def _serve_reports(self) -> None:
        """Resolve delivery futures as reports arrive, until close()."""
        while True:
            await self._producer.poll(0.1)

    async def send(self, envelope: Envelope) -> None:
        """Deliver or raise DependencyUnavailable; never reports success before the ack."""
        try:
            # Ends at the broker ack, so a trace shows produce latency and, up to the worker's
            # span, queue delay.
            with tracer.start_as_current_span("kafka.produce"):
                async with asyncio.timeout(self._timeout_s + 1):
                    delivery = await self._producer.produce(
                        self._topic,
                        value=envelope_bytes(envelope),
                        key=envelope.thread_id.encode(),
                    )
                    await delivery
        except (KafkaException, TimeoutError) as exc:
            raise DependencyUnavailable(f"kafka: {type(exc).__name__}: {exc}") from exc

    async def ready(self) -> None:
        """Raise DependencyUnavailable unless the broker answers and the topic exists."""
        try:
            metadata = await self._producer.list_topics(self._topic, timeout=self._timeout_s)
        except KafkaException as exc:
            raise DependencyUnavailable(f"kafka: {exc}") from exc
        topic = metadata.topics.get(self._topic)
        if topic is None or topic.error is not None:
            raise DependencyUnavailable(f"kafka: topic {self._topic} unavailable")

    async def close(self) -> None:
        """Flush outstanding messages, stop the report task, release the client."""
        await self._producer.flush()
        self._reports.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reports
        await self._producer.close()
