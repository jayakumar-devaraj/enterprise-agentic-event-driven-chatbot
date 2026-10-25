"""Per-message Redis Stream writer (ADR 0005): seq from 0, bounded, expiring, fenced (ADR 0009)."""

import json
from typing import Any
from uuid import UUID

from chat_common.schemas import (
    ApprovalRequiredEvent,
    Citation,
    CitationsEvent,
    DoneEvent,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    TokenEvent,
    stream_key,
)

from worker.lease import FENCE, Lease

_ADD = (
    FENCE
    + """
local id = redis.call('XADD', KEYS[1], 'MAXLEN', '~', ARGV[1], '*', 'data', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
return id
"""
)
_RESET = FENCE + "redis.call('DEL', KEYS[1])"


class StreamWriter:
    """Appends typed events for one message; `seq` counts events written by this writer."""

    def __init__(
        self,
        redis: Any,
        thread_id: str,
        message_id: UUID | str,
        *,
        lease: Lease,
        maxlen: int,
        ttl_s: int,
    ) -> None:
        """Bind to `tokens:{thread_id}:{message_id}`; every write requires `lease` to be held."""
        self._redis = redis
        self.key = stream_key(thread_id, message_id)
        self.lease = lease
        self._maxlen = maxlen
        self._ttl_s = ttl_s
        self.seq = 0
        self._statuses = 0

    @property
    def reply_started(self) -> bool:
        """True once anything but a status note was written: a retry would then repeat output."""
        return self.seq > self._statuses

    async def last_event(self) -> dict[str, Any] | None:
        """The newest event already in the stream, if any (left by an earlier attempt)."""
        rows = await self._redis.xrevrange(self.key, count=1)
        return json.loads(rows[0][1]["data"]) if rows else None

    async def reset(self) -> None:
        """Drop events from an attempt that never finished (lease expired after a crash)."""
        await self.lease.run(self._redis, _RESET, [self.key], [])
        self.seq = 0
        self._statuses = 0

    async def _add(self, event: StreamEvent) -> str:
        """XADD one event as JSON under field `data` and refresh the TTL, in one fenced script."""
        entry_id = await self.lease.run(
            self._redis, _ADD, [self.key], [self._maxlen, event.model_dump_json(), self._ttl_s]
        )
        self.seq += 1
        return str(entry_id)

    async def token(self, token: str) -> str:
        """Append a TokenEvent."""
        return await self._add(TokenEvent(seq=self.seq, token=token))

    async def done(self) -> str:
        """Append the terminal DoneEvent."""
        return await self._add(DoneEvent(seq=self.seq))

    async def error(self, code: str, detail: str) -> str:
        """Append the terminal ErrorEvent."""
        return await self._add(ErrorEvent(seq=self.seq, code=code, detail=detail))

    async def approval_required(self, detail: str) -> str:
        """Append the terminal ApprovalRequiredEvent."""
        return await self._add(ApprovalRequiredEvent(seq=self.seq, detail=detail))

    async def status(self, detail: str) -> str | None:
        """Append a StatusEvent once per attempt; a retry must not repeat the note."""
        if self._statuses:
            return None
        entry_id = await self._add(StatusEvent(seq=self.seq, detail=detail))
        self._statuses += 1
        return entry_id

    async def citations(self, citations: list[Citation]) -> str:
        """Append a CitationsEvent; sent after the last token and before `done`."""
        return await self._add(CitationsEvent(seq=self.seq, citations=citations))
