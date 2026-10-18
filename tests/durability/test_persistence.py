"""Redis and Kafka state survives SIGKILL, container replacement, and a volume backup/restore.

Runs its own compose project (`chatbot-dur`, other host ports); the developer stack is untouched:
    uv run pytest -m durability
"""

import os
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import redis
from confluent_kafka import Consumer, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic

pytestmark = pytest.mark.durability

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "compose.yaml"
PROJECT = "chatbot-dur"
ENV = {**os.environ, "REDIS_HOST_PORT": "26379", "KAFKA_HOST_PORT": "29092"}
REDIS_URL = "redis://127.0.0.1:26379/0"
KAFKA = "127.0.0.1:29092"
REDIS_IMAGE = "redis:8.10.2"


def _compose(*args: str) -> None:
    """docker compose against the isolated project; fail loudly."""
    cmd = ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE), *args]
    subprocess.run(cmd, env=ENV, check=True, capture_output=True, timeout=300)


def _docker(*args: str) -> None:
    """Plain docker command; fail loudly."""
    subprocess.run(["docker", *args], check=True, capture_output=True, timeout=300)


@pytest.fixture(scope="module")
def stack() -> Iterator[None]:
    """Fresh redis + kafka with empty named volumes; removed afterwards."""
    _compose("down", "-v")
    _compose("up", "-d", "--wait", "redis", "kafka")
    yield
    _compose("down", "-v")
    subprocess.run(["docker", "volume", "rm", "-f", f"{PROJECT}_backup"], capture_output=True)


def _write_state(topic: str, group: str, key: str) -> None:
    """A Redis string, stream entry and AOF-only write; a Kafka record and a committed offset."""
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    r.set(f"thread_owner:{key}", "alice")
    r.xadd("audit:events", {"event": "durability_probe", "thread_id": key})
    r.close()

    admin = AdminClient({"bootstrap.servers": KAFKA})
    for fut in admin.create_topics([NewTopic(topic, 1, 1)]).values():
        fut.result(60)
    producer = Producer({"bootstrap.servers": KAFKA, "acks": "all"})
    producer.produce(topic, value=key.encode())
    assert producer.flush(30) == 0
    consumer = Consumer(
        {"bootstrap.servers": KAFKA, "group.id": group, "auto.offset.reset": "earliest"}
    )
    consumer.assign([TopicPartition(topic, 0, 1)])
    consumer.commit(offsets=[TopicPartition(topic, 0, 1)], asynchronous=False)
    consumer.close()
    time.sleep(2)  # appendfsync everysec: let the AOF reach disk before the abrupt kill


def _assert_state(topic: str, group: str, key: str) -> None:
    """Everything written by `_write_state` is readable again."""
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    deadline = time.monotonic() + 60
    while True:
        try:
            r.ping()
            break
        except redis.exceptions.BusyLoadingError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)
    assert r.get(f"thread_owner:{key}") == "alice"
    audit = r.xrange("audit:events")
    assert any(f.get("thread_id") == key for _, f in audit)
    r.close()

    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA,
            "group.id": group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    try:
        (committed,) = consumer.committed([TopicPartition(topic, 0)], timeout=60)
        assert committed.offset == 1
        consumer.assign([TopicPartition(topic, 0, 0)])
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            msg = consumer.poll(1.0)
            if msg is not None and not msg.error():
                assert msg.value() == key.encode()
                return
        raise AssertionError("record not readable after restart")
    finally:
        consumer.close()


def test_sigkill_then_restart(stack) -> None:
    """Abrupt kill of both containers, same containers restarted."""
    key, topic, group = uuid.uuid4().hex[:8], f"dur-{uuid.uuid4().hex[:8]}", uuid.uuid4().hex
    _write_state(topic, group, key)
    _compose("kill", "redis", "kafka")
    _compose("up", "-d", "--wait", "redis", "kafka")
    _assert_state(topic, group, key)


def test_container_replacement(stack) -> None:
    """New containers on the same named volumes (an image upgrade or `--force-recreate`)."""
    key, topic, group = uuid.uuid4().hex[:8], f"dur-{uuid.uuid4().hex[:8]}", uuid.uuid4().hex
    _write_state(topic, group, key)
    _compose("up", "-d", "--wait", "--force-recreate", "redis", "kafka")
    _assert_state(topic, group, key)


def test_redis_volume_backup_and_restore(stack) -> None:
    """The documented backup (tar of the stopped volume) restores into a brand-new volume."""
    key = uuid.uuid4().hex[:8]
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    r.set(f"thread_owner:{key}", "alice")
    r.close()
    time.sleep(2)
    data, backup = f"{PROJECT}_redis-data", f"{PROJECT}_backup"
    _compose("stop", "redis")
    _docker("run", "--rm", "-v", f"{data}:/data:ro", "-v", f"{backup}:/backup", REDIS_IMAGE,
            "tar", "-C", "/data", "-cf", "/backup/redis.tar", ".")  # fmt: skip
    _compose("rm", "-f", "redis")
    _docker("volume", "rm", data)
    _docker("volume", "create", data)
    _docker("run", "--rm", "-v", f"{data}:/data", "-v", f"{backup}:/backup:ro", REDIS_IMAGE,
            "tar", "-C", "/data", "-xf", "/backup/redis.tar")  # fmt: skip
    _compose("up", "-d", "--wait", "redis")
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    assert r.get(f"thread_owner:{key}") == "alice"
    r.close()
