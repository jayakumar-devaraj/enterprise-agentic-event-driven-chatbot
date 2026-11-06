"""DLQ replay clears a failed attempt's stream for every DLQ'd error code (no broker)."""

import json
from typing import Any
from uuid import uuid4

import pytest
from chat_common.schemas import Envelope, stream_key
from worker import dlq


class _Producer:
    """Producer double: records what replay re-produces."""

    produced: list[bytes] = []

    def __init__(self, *_: Any) -> None:
        pass

    def produce(self, topic: str, value: bytes, key: bytes) -> None:
        self.produced.append(key)

    def flush(self, _: float) -> int:
        return 0


class _SyncRedis:
    """Sync redis double holding one stream."""

    def __init__(self, key: str, code: str) -> None:
        self.rows = {key: [("1-0", {"data": json.dumps({"type": "error", "code": code})})]}

    def xrevrange(self, key: str, count: int) -> list[Any]:
        return self.rows.get(key, [])[-count:]

    def delete(self, key: str) -> None:
        self.rows.pop(key, None)


@pytest.mark.parametrize(
    ("code", "deleted"),
    [("dependency_unavailable", True), ("internal_error", True), ("rag_error", False)],
)
def test_replay_deletes_only_a_dlq_error_stream(monkeypatch, code: str, deleted: bool) -> None:
    """A stream ending in a DLQ'd code would be re-recorded, not reprocessed; others are kept."""
    monkeypatch.setattr(dlq, "Producer", _Producer)
    env = Envelope(message_id=uuid4(), thread_id="t1", user_id="u", text="hi", trace_id="x")
    key = stream_key(env.thread_id, env.message_id)
    redis = _SyncRedis(key, code)
    record = dlq.DlqRecord(0, 0, {"envelope": json.loads(env.model_dump_json())})
    assert dlq.replay([record], bootstrap="x", topic="t", redis=redis) == [str(env.message_id)]
    assert (key not in redis.rows) is deleted
