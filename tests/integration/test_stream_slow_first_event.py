"""Regression: the SSE relay must survive XREAD blocks that time out before the first event.

Found during A9: real CPU inference takes longer than STREAM_BLOCK_MS to produce a first token, and
the gateway's Redis client timed out its own blocking read ("Timeout reading from redis:6379"),
closing the stream with no events.
"""

import asyncio
import json
import uuid

import pytest
from chat_common.settings import Settings
from conftest import REDIS_URL
from gateway.app import make_redis
from gateway.sse import relay

pytestmark = pytest.mark.integration


async def test_relay_waits_past_several_block_periods() -> None:
    """First event written after 7 s with 6 s blocks: the relay still delivers it and closes."""
    settings = Settings(redis_url=REDIS_URL, stream_block_ms=6000)
    reader, writer = make_redis(settings), make_redis(settings)
    key = f"tokens:slow-{uuid.uuid4().hex[:8]}:{uuid.uuid4()}"

    async def late_writer() -> None:
        await asyncio.sleep(7)
        await writer.xadd(key, {"data": json.dumps({"type": "done", "seq": 0})})

    task = asyncio.create_task(late_writer())
    try:
        events = [
            e
            async for e in relay(
                reader, key, None, block_ms=settings.stream_block_ms, max_wait_s=30
            )
        ]
    finally:
        await task
        await reader.aclose()
        await writer.aclose()
    assert [e.event for e in events] == ["done"]
