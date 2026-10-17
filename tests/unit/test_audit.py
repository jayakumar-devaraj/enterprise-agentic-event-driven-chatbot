"""Audit log against an in-memory stream test double."""

from typing import Any

from chat_common.audit import AUDIT_STREAM, AuditLog


class FakeStreams:
    """Test double for the two redis.asyncio stream calls AuditLog uses."""

    def __init__(self) -> None:
        """Start with empty streams."""
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.kwargs: list[dict[str, Any]] = []

    async def xadd(self, name: str, fields: dict[str, str], **kwargs: Any) -> str:
        """Append and return a monotonically increasing id."""
        rows = self.streams.setdefault(name, [])
        entry_id = f"{len(rows) + 1}-0"
        rows.append((entry_id, dict(fields)))
        self.kwargs.append(kwargs)
        return entry_id

    async def xrevrange(
        self, name: str, max: str = "+", min: str = "-", count: int | None = None
    ) -> Any:
        """Newest first, limited to count."""
        return list(reversed(self.streams.get(name, [])))[:count]


async def test_record_and_query_newest_first() -> None:
    """Entries are bounded, typed, and filterable by thread."""
    fake = FakeStreams()
    log = AuditLog(fake)
    await log.record("chat_accepted", actor="alice", thread_id="t1", message_id="m1")
    await log.record("refund_proposed", actor="system", thread_id="t2", amount=None)
    await log.record("refund_applied", actor="alice", thread_id="t1", ref="m1")

    assert fake.kwargs[0] == {"maxlen": 100_000, "approximate": True}
    assert list(fake.streams) == [AUDIT_STREAM]
    rows = await log.recent(thread_id="t1")
    assert [r["event"] for r in rows] == ["refund_applied", "chat_accepted"]
    assert rows[1]["details"] == {"message_id": "m1"} and rows[1]["actor"] == "alice"
