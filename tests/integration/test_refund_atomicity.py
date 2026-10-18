"""Refund application under injected Redis write failures: all three records or none (SPEC §10)."""

import uuid
from typing import Any

import pytest
import redis.asyncio as aioredis
from conftest import REDIS_URL
from redis.exceptions import ConnectionError
from worker import approval

pytestmark = pytest.mark.integration

WRITES = {"set", "xadd", "eval", "evalsha"}


class FailNthWrite:
    """Test double around a real client: the Nth write raises, before or after the server ran it."""

    def __init__(self, redis: Any, n: int, after: bool) -> None:
        """Count writes from 1; `after` simulates a reply lost once the server executed."""
        self._redis, self._n, self._after, self._count = redis, n, after, 0

    def __getattr__(self, name: str) -> Any:
        """Proxy everything; intercept write commands."""
        target = getattr(self._redis, name)
        if name not in WRITES:
            return target

        async def call(*args: Any, **kwargs: Any) -> Any:
            self._count += 1
            if self._count == self._n and not self._after:
                raise ConnectionError("injected before write")
            result = await target(*args, **kwargs)
            if self._count == self._n:
                raise ConnectionError("injected after write")
            return result

        return call


async def _state(redis: Any, ref: str) -> tuple[bool, int, int]:
    """(marker present, refund records, refund_applied audit entries) for one message."""
    refunds = await redis.xrange(approval.REFUNDS_STREAM)
    audit = await redis.xrange("audit:events")
    return (
        await redis.exists(f"refund_applied:{ref}") == 1,
        sum(1 for _, f in refunds if f["ref_message_id"] == ref),
        sum(1 for _, f in audit if f["event"] == "refund_applied" and ref in f["details"]),
    )


@pytest.mark.parametrize("after", [False, True], ids=["before", "after"])
@pytest.mark.parametrize("n", [1, 2, 3])
async def test_failed_write_then_retry_records_exactly_once(n: int, after: bool) -> None:
    """Fail the Nth write, retry like the worker does, and require one complete record set."""
    redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    ref, thread = str(uuid.uuid4()), f"rf-{uuid.uuid4().hex[:8]}"
    kwargs = {"thread_id": thread, "ref_message_id": ref, "user_id": "alice"}
    try:
        try:
            await approval.apply_refund(FailNthWrite(redis, n, after), **kwargs)
        except ConnectionError:
            marker, refunds, audits = await _state(redis, ref)
            assert (refunds, audits) == ((1, 1) if marker else (0, 0))
        await approval.apply_refund(redis, **kwargs)
        assert await _state(redis, ref) == (True, 1, 1)
        assert await redis.ttl(f"refund_applied:{ref}") == -1
    finally:
        await redis.aclose()
