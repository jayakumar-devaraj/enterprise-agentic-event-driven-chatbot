"""Dedupe lease with fenced writes (SPEC.md §5, ADR 0009).

A fenced script takes the lease key as its last key and the attempt's token as its last argument,
and fails before any write unless the lease still holds that token.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from redis.exceptions import ResponseError

FENCE = """
if redis.call('GET', KEYS[#KEYS]) ~= ARGV[#ARGV] then
  return redis.error_reply('LEASE_LOST ' .. KEYS[#KEYS])
end
"""
_DONE = FENCE + "redis.call('SET', KEYS[1], 'done', 'EX', ARGV[1])"
_RELEASE = FENCE + "redis.call('DEL', KEYS[1])"


class LeaseLost(Exception):
    """Another attempt now owns the message; this one must stop without committing."""


@dataclass(frozen=True)
class Lease:
    """`dedupe:{message_id}` held with a token unique to this attempt."""

    key: str
    token: str = field(default_factory=lambda: f"processing:{uuid.uuid4()}")

    async def run(self, redis: Any, script: str, keys: list[str], args: list[Any]) -> Any:
        """Run a FENCE-prefixed script with this lease appended; LeaseLost if it is not held."""
        try:
            return await redis.eval(script, len(keys) + 1, *keys, self.key, *args, self.token)
        except ResponseError as exc:
            if str(exc).startswith("LEASE_LOST"):
                raise LeaseLost(str(exc)) from exc
            raise

    async def check(self, redis: Any) -> None:
        """LeaseLost unless still held (not atomic with what follows; ADR 0009)."""
        if await redis.get(self.key) != self.token:
            raise LeaseLost(self.key)

    async def done(self, redis: Any, ttl_s: int) -> None:
        """Mark the message done for `ttl_s`, if still held."""
        await self.run(redis, _DONE, [], [ttl_s])

    async def release(self, redis: Any) -> None:
        """Delete the lease so the message can be redelivered, if still held."""
        await self.run(redis, _RELEASE, [], [])
