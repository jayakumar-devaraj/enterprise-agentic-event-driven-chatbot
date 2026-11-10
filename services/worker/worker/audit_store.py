"""Synchronous audit tier (ADR 0011 decision C): refund events go straight to Postgres.

The Redis script that changes refund state still appends the entry to `audit:events` atomically
with that change. Right after it commits, the worker inserts the same entry here, before the user
sees the outcome and before the Kafka offset can be committed. A failed insert fails the record,
which is retried and then dead-lettered; a redelivery inserts again, and `event_key` makes the
repeat a no-op. The exporter's later copy of the Redis entry carries the same key and is skipped.
"""

import json
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from worker.consumer import TransientDependencyError

INSERT = (
    "INSERT INTO audit_log (event_key, ts, event, actor, thread_id, details) "
    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"
)


class AuditStore:
    """Writes audit entries that must not be lost to the append-only `audit_log` table."""

    def __init__(self, dsn: str, connect_timeout_s: int = 5) -> None:
        """`dsn` is the audit_writer role's URL; an empty one fails here, before any I/O."""
        if not dsn:
            raise ValueError("AUDIT_DATABASE_URL is required: refund events are written to it")
        self._dsn, self._timeout = dsn, connect_timeout_s

    async def _connect(self) -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(
            self._dsn, connect_timeout=self._timeout, autocommit=True
        )

    async def check(self) -> None:
        """Fail startup when the database cannot be reached or the role cannot log in."""
        async with await self._connect() as conn:
            await conn.execute("SELECT 1 FROM audit_log LIMIT 1")

    async def write(self, entry: dict[str, str]) -> None:
        """Insert one entry shaped by `AuditLog.entry(..., key=...)`; a repeat key is a no-op.

        An unreachable database is a dependency fault (retried, then DLQ); any other database
        error propagates as a defect.
        """
        row = (
            entry["event_key"],
            datetime.fromisoformat(entry["ts"]),
            entry["event"],
            entry["actor"],
            entry["thread_id"],
            Jsonb(json.loads(entry["details"])),
        )
        try:
            async with await self._connect() as conn:
                await conn.execute(INSERT, row)
        except psycopg.OperationalError as exc:
            raise TransientDependencyError(f"audit store: {type(exc).__name__}") from exc
