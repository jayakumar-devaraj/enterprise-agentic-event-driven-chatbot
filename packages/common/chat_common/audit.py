"""Durable, queryable runtime audit trail on the Redis stream `audit:events`."""

import json
from datetime import UTC, datetime
from typing import Any, Protocol

AUDIT_STREAM = "audit:events"
AUDIT_MAXLEN = 100_000


class _StreamClient(Protocol):
    """The subset of `redis.asyncio.Redis` the audit log uses."""

    async def xadd(self, name: str, fields: dict[str, str], **kwargs: Any) -> Any: ...

    async def xrevrange(
        self, name: str, max: str = "+", min: str = "-", count: int | None = None
    ) -> Any: ...


class AuditLog:
    """Append user-facing actions and state changes; read them back newest first."""

    def __init__(self, redis: _StreamClient) -> None:
        """Wrap an async Redis client (decode_responses=True expected)."""
        self._redis = redis

    async def record(self, event: str, *, actor: str, thread_id: str, **details: Any) -> str:
        """Append one audit entry and return its stream id."""
        fields = {
            "event": event,
            "actor": actor,
            "thread_id": thread_id,
            "ts": datetime.now(UTC).isoformat(),
            "details": json.dumps(details, default=str, sort_keys=True),
        }
        entry_id = await self._redis.xadd(
            AUDIT_STREAM, fields, maxlen=AUDIT_MAXLEN, approximate=True
        )
        return str(entry_id)

    async def recent(
        self, count: int = 100, *, thread_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return up to `count` newest entries, optionally only for one thread."""
        rows = await self._redis.xrevrange(AUDIT_STREAM, count=count)
        out: list[dict[str, Any]] = []
        for entry_id, fields in rows:
            if thread_id is not None and fields.get("thread_id") != thread_id:
                continue
            out.append(
                {"id": entry_id, **fields, "details": json.loads(fields.get("details", "{}"))}
            )
        return out
