"""Inspect and replay the dead-letter topic (SPEC §7): `python -m worker.dlq list|replay`.

Kafka and Redis come from the usual settings (`KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_DLQ_TOPIC`,
`KAFKA_TOPIC`, `REDIS_URL`). `list` prints one JSON line per record without its message text.
`replay` re-produces records that carry an envelope to the request topic, keyed by thread.
Malformed records carry no envelope and are skipped. Replay is idempotent: an envelope that has
already completed is skipped by the worker's dedupe.
"""

import argparse
import json
import uuid
from dataclasses import dataclass
from typing import Any

import redis
from chat_common.schemas import Envelope, stream_key
from chat_common.settings import Settings
from confluent_kafka import Consumer, KafkaException, Producer, TopicPartition


@dataclass(frozen=True)
class DlqRecord:
    """One dead-letter record and where it sits in the topic."""

    partition: int
    offset: int
    record: dict[str, Any]

    @property
    def envelope(self) -> Envelope | None:
        """The failed envelope; None for a malformed record."""
        raw = self.record.get("envelope")
        return Envelope.model_validate(raw) if raw is not None else None

    def summary(self) -> dict[str, Any]:
        """What `list` prints: position, reason, ids and error, never the message text."""
        env = self.envelope
        return {
            "partition": self.partition,
            "offset": self.offset,
            "reason": self.record["reason"],
            "ts": self.record["ts"],
            "message_id": str(env.message_id) if env else None,
            "thread_id": env.thread_id if env else None,
            "kind": env.kind if env else None,
            "error": self.record["error"][:200],
        }


def read(bootstrap: str, topic: str) -> list[DlqRecord]:
    """Every record currently in `topic`, without joining a group or committing anything."""
    consumer = Consumer(
        {
            "bootstrap.servers": bootstrap,
            "group.id": f"dlq-inspect-{uuid.uuid4().hex}",
            "enable.auto.commit": False,
        }
    )
    records: list[DlqRecord] = []
    try:
        partitions = consumer.list_topics(topic, timeout=10).topics[topic].partitions
        for p in sorted(partitions):
            low, high = consumer.get_watermark_offsets(TopicPartition(topic, p), timeout=10)
            if high <= low:
                continue
            consumer.assign([TopicPartition(topic, p, low)])
            while True:
                msg = consumer.poll(10)
                if msg is None:
                    raise TimeoutError(f"{topic}[{p}] stopped before offset {high - 1}")
                if msg.error():
                    raise KafkaException(msg.error())
                records.append(DlqRecord(p, msg.offset(), json.loads(msg.value())))
                if msg.offset() >= high - 1:
                    break
    finally:
        consumer.close()
    return records


def replay(records: list[DlqRecord], *, bootstrap: str, topic: str, redis: Any) -> list[str]:
    """Re-produce each record's envelope to `topic`; returns the replayed message ids.

    The failed attempt's stream ends in `ErrorEvent{dependency_unavailable}`. The worker would read
    that as an outcome already recorded and dead-letter the message again, so replay deletes that
    stream first. A stream that ends any other way (a later success) is left alone.
    """
    producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True})
    replayed: list[str] = []
    for record in records:
        env = record.envelope
        if env is None:
            continue
        key = stream_key(env.thread_id, env.message_id)
        last = redis.xrevrange(key, count=1)
        if last and json.loads(last[0][1]["data"]).get("code") == "dependency_unavailable":
            redis.delete(key)
        producer.produce(topic, value=env.model_dump_json().encode(), key=env.thread_id.encode())
        replayed.append(str(env.message_id))
    if producer.flush(30):
        raise RuntimeError("Kafka did not acknowledge every replayed record")
    return replayed


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(prog="python -m worker.dlq", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="print one JSON line per dead-letter record")
    replay_cmd = commands.add_parser("replay", help="re-produce dead-lettered envelopes")
    which = replay_cmd.add_mutually_exclusive_group(required=True)
    which.add_argument("--offset", type=int, nargs="+", help="offsets from `list`")
    which.add_argument("--all", action="store_true", help="every record with an envelope")
    args = parser.parse_args(argv)

    settings = Settings()
    records = read(settings.kafka_bootstrap_servers, settings.kafka_dlq_topic)
    if args.command == "list":
        for record in records:
            print(json.dumps(record.summary()))
        return
    chosen = records if args.all else [r for r in records if r.offset in set(args.offset)]
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        for mid in replay(
            chosen,
            bootstrap=settings.kafka_bootstrap_servers,
            topic=settings.kafka_topic,
            redis=client,
        ):
            print(f"replayed {mid}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
