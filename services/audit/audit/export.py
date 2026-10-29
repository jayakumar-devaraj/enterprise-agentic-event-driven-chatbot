"""Export the Redis audit stream to Postgres (ADR 0011, decision C).

Entries are read through a consumer group (at least once) and inserted with their stream id as the
idempotency key, then acknowledged only after the insert committed. A crash between the two
redelivers the entry and the insert is a no-op, so the table holds each entry exactly once. The
Redis stream is a buffer capped at `AUDIT_MAXLEN`; the table is the record.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

import psycopg
from chat_common.audit import AUDIT_STREAM
from psycopg.types.json import Jsonb
from redis.exceptions import ResponseError

log = logging.getLogger("audit.export")

GROUP = "audit-exporter"
INSERT = (
    "INSERT INTO audit_log (stream_id, ts, event, actor, thread_id, details) "
    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (stream_id) DO NOTHING"
)


def to_row(stream_id: str, fields: dict[str, str] | None) -> tuple[Any, ...]:
    """One table row from a stream entry. A damaged entry is kept, never dropped."""
    fields = fields or {}
    try:
        ts = datetime.fromisoformat(fields["ts"])
    except (KeyError, ValueError):
        ts = datetime.fromtimestamp(int(stream_id.split("-")[0]) / 1000, UTC)
    raw = fields.get("details", "{}")
    try:
        details = json.loads(raw)
    except ValueError:
        details = {"unparsed": raw}
    if not isinstance(details, dict):
        details = {"unparsed": raw}
    return (
        stream_id,
        ts,
        fields.get("event", "unknown"),
        fields.get("actor", "unknown"),
        fields.get("thread_id", "unknown"),
        Jsonb(details),
    )


class Exporter:
    """Moves audit entries from a Redis stream into the `audit_log` table."""

    def __init__(
        self,
        redis: Any,
        dsn: str,
        consumer: str,
        *,
        stream: str = AUDIT_STREAM,
        group: str = GROUP,
        batch: int = 100,
        claim_idle_ms: int = 60_000,
    ) -> None:
        """`redis` is an async client with decode_responses=True; `dsn` is the writer role's URL."""
        self._redis, self._dsn, self._consumer = redis, dsn, consumer
        self._stream, self._group, self._batch, self._claim_idle_ms = (
            stream,
            group,
            batch,
            claim_idle_ms,
        )

    async def ensure_group(self) -> None:
        """Create the consumer group and stream if absent; an existing group keeps its place."""
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _pending_from_dead_consumers(self) -> list[tuple[str, dict[str, str] | None]]:
        """Entries delivered to a consumer that never acknowledged them, idle long enough."""
        result = await self._redis.xautoclaim(
            self._stream,
            self._group,
            self._consumer,
            min_idle_time=self._claim_idle_ms,
            start_id="0-0",
            count=self._batch,
        )
        return list(result[1])

    async def export_once(self, block_ms: int | None = None) -> int:
        """Export one batch (reclaimed entries, then new ones); return how many were handled."""
        entries = await self._pending_from_dead_consumers()
        if len(entries) < self._batch:
            reply = await self._redis.xreadgroup(
                self._group,
                self._consumer,
                {self._stream: ">"},
                count=self._batch - len(entries),
                block=block_ms,
            )
            for _, new in reply or []:
                entries.extend(new)
        if not entries:
            return 0
        rows = [to_row(entry_id, fields) for entry_id, fields in entries]
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.transaction():
                cur = conn.cursor()
                await cur.executemany(INSERT, rows)
        await self._redis.xack(self._stream, self._group, *(entry_id for entry_id, _ in entries))
        return len(entries)

    async def lag(self) -> int | None:
        """Entries not yet delivered to this group (None if Redis cannot say)."""
        for info in await self._redis.xinfo_groups(self._stream):
            if info["name"] == self._group:
                return info.get("lag")
        return None
