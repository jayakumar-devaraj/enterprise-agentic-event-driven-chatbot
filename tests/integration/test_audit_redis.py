"""AuditLog against a real Redis 8 (rung 2)."""

import os
import uuid

import pytest
import redis.asyncio as aioredis
from chat_common.audit import AuditLog

pytestmark = pytest.mark.integration


async def test_audit_roundtrip_on_real_redis() -> None:
    """Writes land in audit:events and read back newest first with decoded details."""
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    client = aioredis.from_url(url, decode_responses=True)
    thread = f"it-{uuid.uuid4().hex[:8]}"
    try:
        log = AuditLog(client)
        first = await log.record("chat_accepted", actor="alice", thread_id=thread, n=1)
        second = await log.record("refund_applied", actor="alice", thread_id=thread, n=2)
        rows = await log.recent(count=50, thread_id=thread)
        assert [r["id"] for r in rows[:2]] == [second, first]
        assert rows[0]["details"] == {"n": 2}
    finally:
        await client.aclose()
