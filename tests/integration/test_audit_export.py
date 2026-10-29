"""Audit export on a real Redis 8 and a real Postgres (ADR 0011, decision C).

Needs the compose profile `audit`; IT_AUDIT_WRITER_URL and IT_AUDIT_ADMIN_URL name the writer and
the admin role of `audit-db` (host port 15432). Each test uses its own stream and group, so tests
never see each other's entries.
"""

import asyncio
import os
import sys
import uuid

import psycopg
import pytest
import redis.asyncio as aioredis
from audit.export import Exporter
from chat_common.audit import AuditLog

pytestmark = pytest.mark.integration

if sys.platform == "win32":  # psycopg async cannot run on the default Proactor loop
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

WRITER = os.environ.get("IT_AUDIT_WRITER_URL")
ADMIN = os.environ.get("IT_AUDIT_ADMIN_URL")


@pytest.fixture
async def setup(redis_url: str):
    """A fresh stream, its exporter, and a thread id unique to the test."""
    assert WRITER and ADMIN, (
        "set IT_AUDIT_WRITER_URL and IT_AUDIT_ADMIN_URL (see deploy/compose.yaml)"
    )
    redis = aioredis.from_url(redis_url, decode_responses=True)
    stream = f"audit:test:{uuid.uuid4().hex[:8]}"
    exporter = Exporter(redis, WRITER, "it-1", stream=stream, group="it", claim_idle_ms=0)
    await exporter.ensure_group()
    yield redis, exporter, stream, f"it-{uuid.uuid4().hex[:8]}"
    await redis.delete(stream)
    await redis.aclose()


async def _rows(thread: str) -> list[tuple]:
    async with await psycopg.AsyncConnection.connect(ADMIN) as c:
        cur = await c.execute(
            "SELECT event, actor, details FROM audit_log WHERE thread_id = %s ORDER BY id",
            (thread,),
        )
        return await cur.fetchall()


async def _add(redis, stream: str, event: str, thread: str, **details) -> str:
    """Write an entry exactly as `AuditLog.entry` shapes it, onto the test stream."""
    return await redis.xadd(
        stream, AuditLog.entry(event, actor="alice", thread_id=thread, **details)
    )


async def test_entries_reach_postgres_with_all_fields_and_lag_drops_to_zero(setup):
    redis, exporter, stream, thread = setup
    await _add(redis, stream, "chat_accepted", thread, n=1)
    await _add(redis, stream, "refund_applied", thread, amount=12)
    assert await exporter.lag() == 2
    assert await exporter.export_once() == 2
    assert await _rows(thread) == [
        ("chat_accepted", "alice", {"n": 1}),
        ("refund_applied", "alice", {"amount": 12}),
    ]
    assert await exporter.lag() == 0
    assert await exporter.export_once() == 0  # nothing left, nothing re-exported


async def test_a_crash_between_insert_and_ack_redelivers_without_duplicates(setup):
    redis, exporter, stream, thread = setup
    await _add(redis, stream, "chat_accepted", thread)
    # Deliver the entry to a consumer that then "crashes" before acknowledging it.
    await redis.xreadgroup("it", "dead-consumer", {stream: ">"}, count=10)
    assert await exporter.export_once() == 1  # reclaimed from the dead consumer and inserted
    assert await exporter.export_once() == 0
    # Simulate the other crash window: row committed, ack lost. Re-deliver the same entry again.
    entry_id = (await redis.xrange(stream))[0][0]
    await redis.xgroup_setid(stream, "it", "0")
    await redis.xreadgroup("it", "dead-consumer", {stream: ">"}, count=10)
    assert await exporter.export_once() == 1  # handled again ...
    assert len(await _rows(thread)) == 1  # ... but the insert was a no-op
    assert entry_id


async def test_a_damaged_entry_is_kept_not_dropped_and_does_not_block_the_next(setup):
    redis, exporter, stream, thread = setup
    await redis.xadd(stream, {"event": "odd", "thread_id": thread, "details": "not json"})
    await _add(redis, stream, "chat_accepted", thread)
    assert await exporter.export_once() == 2
    rows = await _rows(thread)
    assert rows[0] == ("odd", "unknown", {"unparsed": "not json"})
    assert rows[1][0] == "chat_accepted"


async def test_exported_rows_survive_the_stream_being_trimmed(setup):
    redis, exporter, stream, thread = setup
    for n in range(5):
        await _add(redis, stream, "chat_accepted", thread, n=n)
    await exporter.export_once()
    await redis.xtrim(stream, maxlen=0)  # the buffer forgets everything
    assert len(await _rows(thread)) == 5


async def test_the_writer_role_can_insert_and_read_but_never_change_or_remove_rows(setup):
    _, exporter, stream, thread = setup
    await _add(setup[0], stream, "chat_accepted", thread)
    await exporter.export_once()
    async with await psycopg.AsyncConnection.connect(WRITER, autocommit=True) as c:
        assert (
            await (
                await c.execute("SELECT count(*) FROM audit_log WHERE thread_id = %s", (thread,))
            ).fetchone()
        )[0] == 1
        for sql in (
            "UPDATE audit_log SET actor = 'x'",
            "DELETE FROM audit_log",
            "TRUNCATE audit_log",
            "DROP TABLE audit_log",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await c.execute(sql)
