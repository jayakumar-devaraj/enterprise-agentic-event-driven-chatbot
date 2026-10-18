"""Async Kafka producer for chat envelopes (confluent-kafka asyncio, ADR 0003)."""

import asyncio
from typing import Protocol

from chat_common.schemas import Envelope
from confluent_kafka import KafkaException
from confluent_kafka.aio import AIOProducer


class DependencyUnavailable(Exception):
    """An infrastructure dependency failed; the API maps this to 503."""


class EnvelopeSink(Protocol):
    """What the app needs from a producer (lets tests use a double)."""

    async def send(self, envelope: Envelope) -> None: ...

    async def ready(self) -> None: ...

    async def close(self) -> None: ...


class KafkaEnvelopeProducer:
    """Produces keyed by `thread_id` and waits for the broker's delivery report."""

    def __init__(self, bootstrap: str, topic: str, timeout_s: float = 5.0) -> None:
        """Configure idempotent delivery with a bounded delivery timeout."""
        self._topic = topic
        self._timeout_s = timeout_s
        self._producer = AIOProducer(
            {
                "bootstrap.servers": bootstrap,
                "enable.idempotence": True,
                "message.timeout.ms": int(timeout_s * 1000),
            }
        )

    async def send(self, envelope: Envelope) -> None:
        """Deliver or raise DependencyUnavailable; never reports success before the ack."""
        try:
            async with asyncio.timeout(self._timeout_s + 1):
                delivery = await self._producer.produce(
                    self._topic,
                    value=envelope.model_dump_json().encode(),
                    key=envelope.thread_id.encode(),
                )
                await delivery
        except (KafkaException, TimeoutError) as exc:
            raise DependencyUnavailable(f"kafka: {exc}") from exc

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
        """Flush outstanding messages and release the client."""
        await self._producer.flush()
        await self._producer.close()
